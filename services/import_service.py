import asyncio
import random
from datetime import datetime, timedelta
import logging
from telethon.tl.functions.contacts import ImportContactsRequest, AddContactRequest, ResolvePhoneRequest
from telethon.tl.types import (
    InputPhoneContact, InputUser,
    UserStatusOnline, UserStatusOffline, UserStatusRecently,
    UserStatusLastWeek, UserStatusLastMonth, UserStatusEmpty
)
from telethon.errors import PeerFloodError, FloodWaitError

logger = logging.getLogger(__name__)
from models import Contact, Account, ImportHistory
from database import db
from services.telegram_service import telegram_manager
from services.flood_manager import record_flood, auto_clear_expired_floods
from services.delay_engine import human_delay

_cancel_flags = {}


def cancel_import(job_id):
    _cancel_flags[job_id] = True


def _get_last_seen(user):
    """Extract last seen category from user status."""
    s = user.status
    if s is None or isinstance(s, UserStatusEmpty):
        return "unknown"
    if isinstance(s, UserStatusOnline):
        return "online"
    if isinstance(s, UserStatusRecently):
        return "recently"
    if isinstance(s, UserStatusOffline):
        # Check how long ago
        if s.was_online:
            delta = datetime.utcnow() - s.was_online
            if delta.days <= 3:
                return "recently"
            elif delta.days <= 7:
                return "last_week"
            elif delta.days <= 30:
                return "last_month"
            else:
                return "long_ago"
        return "unknown"
    if isinstance(s, UserStatusLastWeek):
        return "last_week"
    if isinstance(s, UserStatusLastMonth):
        return "last_month"
    return "unknown"


async def _try_resolve_user(client, phone, tg_username=None):
    """Try to resolve a phone number to a Telegram user using multiple methods.
    Returns the user object or None."""
    user = None

    # Method 1: Try get_entity with phone (local cache + phone book)
    try:
        full_user = await client.get_entity(phone)
        if full_user and hasattr(full_user, 'id'):
            return full_user
    except Exception:
        pass

    # Method 2: Try by username if available
    if tg_username:
        try:
            full_user = await client.get_entity(tg_username)
            if full_user and hasattr(full_user, 'id'):
                logger.info(f"Resolved {phone} via username @{tg_username}")
                return full_user
        except Exception:
            pass

    # Method 3: ImportContactsRequest
    client_id = random.randint(0, 9999999)
    try:
        result = await client(ImportContactsRequest([
            InputPhoneContact(
                client_id=client_id,
                phone=phone,
                first_name="Contact",
                last_name=""
            )
        ]))

        if result.users:
            return result.users[0]
        elif result.retry_contacts:
            await asyncio.sleep(random.uniform(2, 5))
            retry_result = await client(ImportContactsRequest([
                InputPhoneContact(
                    client_id=client_id + 1,
                    phone=phone,
                    first_name="Contact",
                    last_name=""
                )
            ]))
            if retry_result.users:
                return retry_result.users[0]
    except (PeerFloodError, FloodWaitError):
        raise  # Let caller handle flood errors
    except Exception as e:
        logger.debug(f"ImportContactsRequest failed for {phone}: {e}")

    # Method 4: ResolvePhoneRequest
    try:
        phone_stripped = phone.lstrip('+')
        resolve_result = await client(ResolvePhoneRequest(phone=phone_stripped))
        if resolve_result.users:
            logger.info(f"ResolvePhoneRequest found {phone}")
            return resolve_result.users[0]
    except (PeerFloodError, FloodWaitError):
        raise
    except Exception as e:
        logger.debug(f"ResolvePhoneRequest failed for {phone}: {e}")

    # Method 5: Try get_input_entity with phone (different code path in Telethon)
    try:
        entity = await client.get_input_entity(phone)
        if entity:
            # get_input_entity returns InputPeerUser, get full user
            full_user = await client.get_entity(entity)
            if full_user and hasattr(full_user, 'id'):
                logger.info(f"get_input_entity found {phone}")
                return full_user
    except Exception:
        pass

    return None


async def _scan_one(client, account, contact, all_clients=None):
    """Scan a single contact. Tries primary account first, then falls back to other accounts.
    all_clients is a dict of {account_id: client} for fallback attempts."""
    status = "error"
    tg_name = ""
    tg_username = ""
    last_seen = "unknown"
    error_msg = None
    flood = False

    try:
        user = None
        tg_username_hint = contact.tg_username or None

        # Try the assigned account first
        user = await _try_resolve_user(client, contact.phone, tg_username_hint)

        # If not found, try ALL other connected accounts
        if user is None and all_clients:
            for other_aid, other_client in all_clients.items():
                if other_aid == account.id:
                    continue  # Skip the one we already tried
                if not other_client or not other_client.is_connected():
                    continue
                try:
                    user = await _try_resolve_user(other_client, contact.phone, tg_username_hint)
                    if user:
                        logger.info(f"Found {contact.phone} via fallback account {other_aid}")
                        break
                except (PeerFloodError, FloodWaitError):
                    continue  # Skip flooded accounts, try next
                except Exception:
                    continue

        if user:
            tg_name = (user.first_name or "") + (" " + user.last_name if user.last_name else "")
            tg_name = tg_name.strip()
            tg_username = user.username or ""
            last_seen = _get_last_seen(user)

            # Add to ALL connected accounts' contacts so any account can message them later
            added_to_any = False
            clients_to_add = [(account.id, client)]
            if all_clients:
                for aid, c in all_clients.items():
                    if aid != account.id and c and c.is_connected():
                        clients_to_add.append((aid, c))

            for aid, c in clients_to_add:
                try:
                    # First import as contact so this account can resolve them
                    import_result = await c(ImportContactsRequest([
                        InputPhoneContact(
                            client_id=random.randint(0, 9999999),
                            phone=contact.phone,
                            first_name=user.first_name or "Contact",
                            last_name=user.last_name or ""
                        )
                    ]))
                    # Then add with full user info for privacy exception
                    await c(AddContactRequest(
                        id=InputUser(user_id=user.id, access_hash=user.access_hash),
                        first_name=user.first_name or "Contact",
                        last_name=user.last_name or "",
                        phone=contact.phone,
                        add_phone_privacy_exception=True
                    ))
                    added_to_any = True
                except Exception as e:
                    logger.debug(f"AddContact failed for {contact.phone} on account {aid}: {e}")

            if not added_to_any:
                logger.warning(f"Could not add {contact.phone} to any account's contacts")

            status = "added"
            contact.telegram_status = "on_telegram"
            contact.tg_name = tg_name
            contact.tg_username = tg_username
            contact.tg_user_id = user.id
            contact.tg_last_seen = last_seen
        else:
            status = "not_found"
            contact.telegram_status = "not_found"

    except PeerFloodError:
        status = "peer_flood"
        flood = True
        hours = record_flood(account)
        error_msg = f"PEER_FLOOD (cooldown: {hours}h)"

    except FloodWaitError as e:
        status = "flood_wait"
        error_msg = f"FLOOD_WAIT_{e.seconds}s"
        await asyncio.sleep(e.seconds)

    except Exception as e:
        status = "error"
        error_msg = str(e)
        contact.telegram_status = "error"

    history = ImportHistory(
        contact_id=contact.id,
        account_id=account.id,
        status=status,
        tg_name=tg_name,
        tg_username=tg_username,
        error_msg=error_msg
    )
    db.session.add(history)

    return {
        "phone": contact.phone,
        "status": status,
        "tg_name": tg_name,
        "tg_username": tg_username,
        "last_seen": last_seen,
        "account": account.phone,
        "message": error_msg or "OK",
        "flood": flood,
        "account_id": account.id
    }


async def bulk_import(job_id, contact_ids, account_ids, delay_min, delay_max, progress_queue):
    """Import contacts in parallel batches — one contact per account simultaneously."""
    with telegram_manager.app.app_context():
        # Auto-clear expired flood limits
        auto_clear_expired_floods()

        # Build active accounts list
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

        contacts = Contact.query.filter(Contact.id.in_(contact_ids)).all()
        total = len(contacts)
        processed = 0
        found = 0
        not_found = 0

        # Build map of all connected clients for cross-account fallback
        all_clients = {}
        for a in active_accounts:
            c = telegram_manager.get_client(a.id)
            if c:
                all_clients[a.id] = c

        # Process in batches of len(active_accounts)
        i = 0
        while i < total:
            if _cancel_flags.get(job_id):
                progress_queue.put({"current": processed, "total": total, "status": "cancelled", "message": "Import cancelled by user"})
                _cancel_flags.pop(job_id, None)
                progress_queue.put(None)
                return

            batch_size = len(active_accounts)
            batch = contacts[i:i + batch_size]

            # Fire all scans in this batch concurrently
            tasks = []
            for idx, contact in enumerate(batch):
                account = active_accounts[idx % len(active_accounts)]
                client = telegram_manager.get_client(account.id)
                tasks.append(_scan_one(client, account, contact, all_clients))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            flooded_ids = set()
            for r in results:
                processed += 1
                if isinstance(r, Exception):
                    progress_queue.put({
                        "current": processed, "total": total,
                        "phone": "?", "status": "error",
                        "message": str(r)
                    })
                    continue

                if r["status"] == "added":
                    found += 1
                elif r["status"] == "not_found":
                    not_found += 1

                if r["flood"]:
                    flooded_ids.add(r["account_id"])

                r["current"] = processed
                r["total"] = total
                del r["flood"]
                del r["account_id"]
                progress_queue.put(r)

            db.session.commit()

            # Remove flooded accounts
            if flooded_ids:
                active_accounts = [a for a in active_accounts if a.id not in flooded_ids]
                if not active_accounts:
                    progress_queue.put({
                        "current": processed, "total": total,
                        "status": "peer_flood",
                        "message": "All accounts flood-limited. Stopping."
                    })
                    progress_queue.put(None)
                    return

            i += batch_size

            # Delay between batches (not after the last one)
            if i < total:
                delay = human_delay(delay_min, delay_max)
                await asyncio.sleep(delay)

        progress_queue.put({
            "current": total, "total": total,
            "status": "complete",
            "message": f"Done. Found: {found}, Not found: {not_found}"
        })
        progress_queue.put(None)
