import asyncio
import logging
import random
from datetime import datetime, timedelta
from telethon.errors import PeerFloodError, UserNotMutualContactError, FloodWaitError
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.tl.types import InputPhoneContact
from models import Contact, Account, Message, SendHistory, ChatMessage, ImportHistory
from database import db
from services.telegram_service import telegram_manager
from services.spintax import spin
from services.delay_engine import human_delay, should_take_break
from services.flood_manager import record_flood, auto_clear_expired_floods

logger = logging.getLogger(__name__)

_cancel_flags = {}
_db_lock = asyncio.Lock()


def cancel_send(job_id):
    _cancel_flags[job_id] = True


def _get_effective_limit(account):
    """Return the effective daily limit, accounting for warmup."""
    base_limit = account.daily_limit or 20

    if not account.warmup_start_date:
        return base_limit

    days_since = (datetime.utcnow() - account.warmup_start_date).days

    # Warmup schedule: day 0-1: 5, day 2: 10, day 3: 15, day 4+: full limit
    warmup_schedule = {0: 5, 1: 5, 2: 10, 3: 15}
    warmup_limit = warmup_schedule.get(days_since, base_limit)

    return min(warmup_limit, base_limit)


def get_remaining_capacity(account_id):
    """Return how many more messages this account can send today."""
    account = db.session.get(Account, account_id)
    if not account:
        return 0
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = SendHistory.query.filter(
        SendHistory.account_id == account_id,
        SendHistory.status == 'sent',
        SendHistory.sent_at >= today_start
    ).count()
    effective_limit = _get_effective_limit(account)
    return max(0, effective_limit - today_count)


async def _send_one(client, account, contact, message, rendered, stagger_delay=0):
    """Send a single message to a single contact. Returns result dict."""
    if stagger_delay > 0:
        await asyncio.sleep(stagger_delay)

    status = "failed"
    error_msg = None
    flood = False

    try:
        entity = None

        # 1) Try tg_user_id first
        if contact.tg_user_id:
            try:
                entity = await client.get_input_entity(contact.tg_user_id)
            except (ValueError, TypeError):
                pass

        # 2) Try phone number directly
        if entity is None and contact.phone:
            try:
                entity = await client.get_input_entity(contact.phone)
            except (ValueError, TypeError):
                pass

        # 3) Import as contact to resolve phone -> user
        if entity is None and contact.phone:
            try:
                import_result = await client(ImportContactsRequest([
                    InputPhoneContact(
                        client_id=0,
                        phone=contact.phone,
                        first_name=contact.name or "User",
                        last_name=""
                    )
                ]))
                if import_result.users:
                    entity = import_result.users[0]
            except Exception as e:
                logger.warning(f"ImportContactsRequest fallback failed for {contact.phone}: {e}")

        if entity is None:
            raise ValueError(f"Cannot resolve user for {contact.phone}")

        # Extract actual tg_user_id from resolved entity
        resolved_user_id = contact.tg_user_id
        if resolved_user_id is None:
            if hasattr(entity, 'user_id'):
                resolved_user_id = entity.user_id
            elif hasattr(entity, 'id'):
                resolved_user_id = entity.id

        result = await client.send_message(entity, rendered)
        status = "sent"
        contact.last_send_status = "sent"
        contact.last_send_at = datetime.utcnow()

        if resolved_user_id and not contact.tg_user_id:
            contact.tg_user_id = resolved_user_id

        # Store as chat message
        tg_name = contact.tg_username or (contact.tg_name if contact.tg_name and contact.tg_name != 'Contact' else None) or contact.name
        chat_msg = ChatMessage(
            account_id=account.id,
            contact_id=contact.id,
            tg_user_id=resolved_user_id or 0,
            tg_name=tg_name,
            tg_username=contact.tg_username,
            direction='outgoing',
            text=rendered,
            tg_message_id=result.id if result else None,
            is_read=True,
            created_at=datetime.utcnow()
        )
        db.session.add(chat_msg)

    except PeerFloodError:
        status = "peer_flood"
        flood = True
        hours = record_flood(account)
        error_msg = f"PEER_FLOOD (cooldown: {hours}h)"
        contact.last_send_status = "peer_flood"

    except FloodWaitError as e:
        status = "flood_wait"
        error_msg = f"FLOOD_WAIT_{e.seconds}s"
        await asyncio.sleep(e.seconds)

    except (UserNotMutualContactError, ValueError) as e:
        status = "not_on_telegram"
        error_msg = str(e)
        contact.last_send_status = "not_on_telegram"
        contact.telegram_status = "not_found"

    except Exception as e:
        status = "failed"
        error_msg = str(e)
        contact.last_send_status = "failed"

    async with _db_lock:
        _record(contact, account, message, status, error_msg, rendered)
        db.session.commit()

    return {
        "phone": contact.phone,
        "name": contact.name,
        "status": status,
        "message": error_msg or "OK",
        "account": account.phone,
        "flood": flood,
        "account_id": account.id
    }


async def bulk_send(job_id, contact_ids, message_id, account_ids,
                    delay_min, delay_max, progress_queue,
                    per_account_limit=0, scan_gap_minutes=0):
    """Send messages with each account running independently on its own random delay."""
    with telegram_manager.app.app_context():
        # Auto-clear any expired flood limits before starting
        cleared = auto_clear_expired_floods()
        if cleared:
            logger.info(f"Auto-cleared {cleared} expired flood limits")

        message = db.session.get(Message, message_id)
        contacts = Contact.query.filter(Contact.id.in_(contact_ids)).all()

        # Filter out contacts scanned too recently (scan-to-send gap)
        if scan_gap_minutes > 0:
            cutoff = datetime.utcnow() - timedelta(minutes=scan_gap_minutes)
            too_recent_ids = set()
            for contact in contacts:
                latest_import = ImportHistory.query.filter(
                    ImportHistory.contact_id == contact.id,
                    ImportHistory.status == 'added',
                    ImportHistory.imported_at > cutoff
                ).first()
                if latest_import:
                    too_recent_ids.add(contact.id)

            if too_recent_ids:
                skipped = len(too_recent_ids)
                contacts = [c for c in contacts if c.id not in too_recent_ids]
                progress_queue.put({
                    "status": "info",
                    "message": f"Skipped {skipped} contacts imported within last {scan_gap_minutes} min (scan-to-send gap)"
                })

        active_accounts = []
        for aid in account_ids:
            a = db.session.get(Account, aid)
            if a and a.is_active and not a.is_flood_limited:
                if telegram_manager.get_client(a.id):
                    active_accounts.append(a)

        if not active_accounts:
            progress_queue.put({"error": "No active connected accounts available"})
            progress_queue.put(None)
            return

        total = len(contacts)
        active_ids = {a.id for a in active_accounts}

        # Find which account scanned each contact
        scan_map = {}
        for contact in contacts:
            scan = ImportHistory.query.filter(
                ImportHistory.contact_id == contact.id,
                ImportHistory.account_id.in_(active_ids),
                ImportHistory.status == 'added'
            ).order_by(ImportHistory.imported_at.desc()).first()
            if scan:
                scan_map[contact.id] = scan.account_id

        # Distribute contacts to accounts
        account_queues = {a.id: [] for a in active_accounts}
        unmatched = []
        for contact in contacts:
            assigned_aid = scan_map.get(contact.id)
            if assigned_aid and assigned_aid in active_ids:
                account_queues[assigned_aid].append(contact)
            else:
                unmatched.append(contact)

        for idx, contact in enumerate(unmatched):
            account = active_accounts[idx % len(active_accounts)]
            account_queues[account.id].append(contact)

        # Apply per-account limits (smart: min of user limit and daily remaining capacity)
        for aid in account_queues:
            remaining = get_remaining_capacity(aid)
            if per_account_limit > 0:
                effective = min(per_account_limit, remaining)
            else:
                effective = remaining
            account_queues[aid] = account_queues[aid][:effective]

        actual_total = sum(len(q) for q in account_queues.values())
        if actual_total < total:
            skipped_by_limit = total - actual_total
            if skipped_by_limit > 0:
                progress_queue.put({
                    "status": "info",
                    "message": f"Capped to {actual_total} contacts due to daily limits ({skipped_by_limit} skipped)"
                })
            total = actual_total

        if total == 0:
            progress_queue.put({"error": "No contacts to send — all accounts at daily limit or no contacts remain"})
            progress_queue.put(None)
            return

        progress = {"processed": 0, "total": total}

        async def _account_worker(account, contacts_for_account):
            """Each account sends its contacts sequentially with human-like delays."""
            client = telegram_manager.get_client(account.id)

            for i, contact in enumerate(contacts_for_account):
                if _cancel_flags.get(job_id):
                    return "cancelled"

                # Render template with spintax
                try:
                    rendered = message.template_text.replace("{name}", contact.name or "")
                    rendered = rendered.replace("{token}", contact.token or "")
                    rendered = rendered.replace("{link}", contact.link or "")
                    rendered = spin(rendered)
                except Exception:
                    rendered = message.template_text

                result = await _send_one(client, account, contact, message, rendered)

                progress["processed"] += 1

                if isinstance(result, Exception):
                    progress_queue.put({
                        "current": progress["processed"], "total": progress["total"],
                        "phone": "?", "status": "error",
                        "message": str(result)
                    })
                    continue

                if result["flood"]:
                    result["current"] = progress["processed"]
                    result["total"] = progress["total"]
                    del result["flood"]
                    del result["account_id"]
                    progress_queue.put(result)
                    return "flooded"

                result["current"] = progress["processed"]
                result["total"] = progress["total"]
                del result["flood"]
                del result["account_id"]
                progress_queue.put(result)

                # Check daily limit after each send
                if result["status"] == "sent":
                    remaining = get_remaining_capacity(account.id)
                    if remaining <= 0:
                        progress_queue.put({
                            "status": "info",
                            "message": f"Account {account.phone} reached daily limit, stopping"
                        })
                        return "daily_limit"

                # Human-like delay before next send
                if i < len(contacts_for_account) - 1:
                    if _cancel_flags.get(job_id):
                        return "cancelled"
                    delay = human_delay(delay_min, delay_max)

                    # Occasional longer break
                    break_time = should_take_break(i)
                    if break_time > 0:
                        progress_queue.put({
                            "status": "info",
                            "message": f"Account {account.phone} taking a {int(break_time)}s break..."
                        })
                        delay += break_time

                    await asyncio.sleep(delay)

            return "done"

        async def _staggered_worker(account, contacts_for_account, stagger):
            if stagger > 0:
                await asyncio.sleep(stagger)
            return await _account_worker(account, contacts_for_account)

        tasks = []
        for idx, account in enumerate(active_accounts):
            worker_contacts = account_queues[account.id]
            if not worker_contacts:
                continue
            stagger = random.uniform(1, 5) * idx if idx > 0 else 0
            tasks.append(_staggered_worker(account, worker_contacts, stagger))

        if not tasks:
            progress_queue.put({"error": "No contacts to send after applying limits"})
            progress_queue.put(None)
            return

        results = await asyncio.gather(*tasks, return_exceptions=True)

        if _cancel_flags.get(job_id):
            progress_queue.put({
                "current": progress["processed"], "total": progress["total"],
                "status": "cancelled", "message": "Send cancelled by user"
            })
            _cancel_flags.pop(job_id, None)

        progress_queue.put(None)


def _record(contact, account, message, status, error_msg, rendered):
    h = SendHistory(
        contact_id=contact.id,
        account_id=account.id,
        message_id=message.id if message else None,
        status=status,
        error_msg=error_msg,
        rendered_message=rendered
    )
    db.session.add(h)
