import queue
import logging
import threading
from datetime import datetime
from telethon import events
from telethon.tl.types import UpdateReadHistoryOutbox
from database import db
from models import ChatMessage, Contact

logger = logging.getLogger(__name__)

# SSE subscribers for real-time chat updates
_chat_sse_subscribers = []
_sse_lock = threading.Lock()


def subscribe_sse():
    q = queue.Queue()
    with _sse_lock:
        _chat_sse_subscribers.append(q)
    return q


def unsubscribe_sse(q):
    with _sse_lock:
        try:
            _chat_sse_subscribers.remove(q)
        except ValueError:
            pass


def _broadcast_sse(event_data):
    with _sse_lock:
        for q in _chat_sse_subscribers:
            try:
                q.put_nowait(event_data)
            except queue.Full:
                pass


def register_incoming_handler(account_id, client):
    """Register a Telethon event handler for incoming private messages."""
    @client.on(events.NewMessage(incoming=True))
    async def handler(event):
        if not event.is_private:
            return

        sender = await event.get_sender()
        if not sender or sender.bot:
            return

        sender_id = sender.id
        sender_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
        sender_username = sender.username or ""
        text = event.message.text or ""
        tg_message_id = event.message.id

        if not text:
            return

        from services.telegram_service import telegram_manager
        with telegram_manager.app.app_context():
            contact = Contact.query.filter_by(tg_user_id=sender_id).first()
            contact_id = contact.id if contact else None
            display_name = contact.name if contact else sender_name
            display_username = contact.tg_username if contact else sender_username

            msg = ChatMessage(
                account_id=account_id,
                contact_id=contact_id,
                tg_user_id=sender_id,
                tg_name=display_name or sender_name,
                tg_username=display_username or sender_username,
                direction='incoming',
                text=text,
                tg_message_id=tg_message_id,
                is_read=False,
                created_at=datetime.utcnow()
            )
            db.session.add(msg)
            db.session.commit()

            _broadcast_sse({
                "type": "new_message",
                "message": {
                    "id": msg.id,
                    "account_id": account_id,
                    "contact_id": contact_id,
                    "tg_user_id": sender_id,
                    "tg_name": msg.tg_name,
                    "tg_username": msg.tg_username,
                    "direction": "incoming",
                    "text": text,
                    "is_read": False,
                    "delivery_status": "sent",
                    "created_at": msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
                }
            })

    # Handler for when the recipient reads our outgoing messages
    @client.on(events.Raw(types=[UpdateReadHistoryOutbox]))
    async def read_handler(event):
        """Fires when a user reads our messages (outbox read pointer advances)."""
        try:
            from services.telegram_service import telegram_manager

            # Get the peer user_id from the event
            peer = event.peer
            from telethon.tl.types import PeerUser
            if not isinstance(peer, PeerUser):
                return

            tg_user_id = peer.user_id
            max_id = event.max_id  # all messages up to this ID have been read

            with telegram_manager.app.app_context():
                # Mark all outgoing messages to this user (up to max_id) as "read"
                updated = ChatMessage.query.filter(
                    ChatMessage.account_id == account_id,
                    ChatMessage.tg_user_id == tg_user_id,
                    ChatMessage.direction == 'outgoing',
                    ChatMessage.tg_message_id <= max_id,
                    ChatMessage.delivery_status != 'read'
                ).update({"delivery_status": "read"})

                if updated:
                    db.session.commit()
                    _broadcast_sse({
                        "type": "read_receipt",
                        "tg_user_id": tg_user_id,
                        "max_id": max_id,
                        "account_id": account_id
                    })
        except Exception as e:
            logger.warning(f"read_handler error for account {account_id}: {e}")

    if not hasattr(client, '_chat_handlers'):
        client._chat_handlers = []
    client._chat_handlers.append(handler)
    client._chat_handlers.append(read_handler)


def unregister_incoming_handler(client):
    """Remove all chat handlers from a client."""
    if hasattr(client, '_chat_handlers'):
        for handler in client._chat_handlers:
            client.remove_event_handler(handler)
        client._chat_handlers.clear()


async def check_read_status():
    """Actively check read status for all outgoing messages by querying Telegram dialogs.

    For each connected account, get dialogs and compare read_outbox_max_id
    to update delivery_status of our stored messages.
    """
    from services.telegram_service import telegram_manager
    import logging
    logger = logging.getLogger(__name__)

    updated_users = []

    for account_id, client in list(telegram_manager._clients.items()):
        if not client or not client.is_connected():
            continue

        try:
            dialogs = await client.get_dialogs(limit=100)
            for dialog in dialogs:
                if not dialog.is_user:
                    continue

                try:
                    tg_user_id = dialog.entity.id
                    # dialog.dialog is the TL Dialog object with read_outbox_max_id
                    read_max = getattr(dialog.dialog, 'read_outbox_max_id', 0) or 0
                except (AttributeError, TypeError):
                    continue

                if read_max > 0:
                    with telegram_manager.app.app_context():
                        count = ChatMessage.query.filter(
                            ChatMessage.account_id == account_id,
                            ChatMessage.tg_user_id == tg_user_id,
                            ChatMessage.direction == 'outgoing',
                            ChatMessage.tg_message_id.isnot(None),
                            ChatMessage.tg_message_id <= read_max,
                            ChatMessage.delivery_status != 'read'
                        ).update({"delivery_status": "read"})

                        if count:
                            db.session.commit()
                            updated_users.append({
                                "tg_user_id": tg_user_id,
                                "max_id": read_max,
                                "account_id": account_id
                            })
        except Exception as e:
            logger.warning(f"check_read_status error for account {account_id}: {e}")
            continue

    # Broadcast all updates via SSE
    for u in updated_users:
        _broadcast_sse({
            "type": "read_receipt",
            "tg_user_id": u["tg_user_id"],
            "max_id": u["max_id"],
            "account_id": u["account_id"]
        })

    return len(updated_users)


async def fetch_missed_messages(account_id, client):
    """Fetch incoming messages we missed while disconnected.

    For each user we've sent messages to, check if they replied
    and store any messages we don't already have.
    """
    from services.telegram_service import telegram_manager

    try:
        with telegram_manager.app.app_context():
            # Get all unique tg_user_ids we have conversations with
            known_users = db.session.query(ChatMessage.tg_user_id).filter(
                ChatMessage.account_id == account_id
            ).distinct().all()
            known_user_ids = {u[0] for u in known_users}

        if not known_user_ids:
            return 0

        fetched = 0
        dialogs = await client.get_dialogs(limit=100)

        for dialog in dialogs:
            if not dialog.is_user:
                continue

            tg_user_id = dialog.entity.id
            if tg_user_id not in known_user_ids:
                continue

            # Get the latest message ID we have from this user
            with telegram_manager.app.app_context():
                last_msg = ChatMessage.query.filter(
                    ChatMessage.account_id == account_id,
                    ChatMessage.tg_user_id == tg_user_id,
                    ChatMessage.tg_message_id.isnot(None)
                ).order_by(ChatMessage.tg_message_id.desc()).first()

                min_id = last_msg.tg_message_id if last_msg else 0

            # Fetch recent messages from this dialog
            messages = await client.get_messages(dialog.entity, limit=50, min_id=min_id)

            for tg_msg in messages:
                if not tg_msg.text:
                    continue
                # Only incoming messages (not from us)
                if tg_msg.out:
                    continue

                with telegram_manager.app.app_context():
                    # Check if we already have this message
                    exists = ChatMessage.query.filter_by(
                        account_id=account_id,
                        tg_user_id=tg_user_id,
                        tg_message_id=tg_msg.id
                    ).first()
                    if exists:
                        continue

                    contact = Contact.query.filter_by(tg_user_id=tg_user_id).first()
                    sender = tg_msg.sender
                    sender_name = ""
                    sender_username = ""
                    if sender:
                        sender_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
                        sender_username = sender.username or ""

                    msg = ChatMessage(
                        account_id=account_id,
                        contact_id=contact.id if contact else None,
                        tg_user_id=tg_user_id,
                        tg_name=(contact.name if contact else None) or sender_name,
                        tg_username=(contact.tg_username if contact else None) or sender_username,
                        direction='incoming',
                        text=tg_msg.text,
                        tg_message_id=tg_msg.id,
                        is_read=False,
                        created_at=tg_msg.date or datetime.utcnow()
                    )
                    db.session.add(msg)
                    db.session.commit()
                    fetched += 1

                    _broadcast_sse({
                        "type": "new_message",
                        "message": {
                            "id": msg.id,
                            "account_id": account_id,
                            "contact_id": msg.contact_id,
                            "tg_user_id": tg_user_id,
                            "tg_name": msg.tg_name,
                            "tg_username": msg.tg_username,
                            "direction": "incoming",
                            "text": msg.text,
                            "is_read": False,
                            "delivery_status": "sent",
                            "created_at": msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
                        }
                    })

        logger.info(f"Fetched {fetched} missed messages for account {account_id}")
        return fetched

    except Exception as e:
        logger.warning(f"fetch_missed_messages error for account {account_id}: {e}")
        return 0


async def send_chat_message(account_id, tg_user_id, text):
    """Send a message and store it as outgoing."""
    from services.telegram_service import telegram_manager
    client = telegram_manager.get_client(account_id)
    if not client:
        raise ValueError("Account not connected")

    entity = await client.get_input_entity(tg_user_id)
    result = await client.send_message(entity, text)

    with telegram_manager.app.app_context():
        contact = Contact.query.filter_by(tg_user_id=tg_user_id).first()
        msg = ChatMessage(
            account_id=account_id,
            contact_id=contact.id if contact else None,
            tg_user_id=tg_user_id,
            tg_name=contact.name if contact else None,
            tg_username=contact.tg_username if contact else None,
            direction='outgoing',
            text=text,
            tg_message_id=result.id,
            is_read=True,
            created_at=datetime.utcnow()
        )
        db.session.add(msg)
        db.session.commit()

        return {
            "id": msg.id,
            "account_id": account_id,
            "contact_id": msg.contact_id,
            "tg_user_id": tg_user_id,
            "direction": "outgoing",
            "text": text,
            "is_read": True,
            "delivery_status": "sent",
            "created_at": msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        }
