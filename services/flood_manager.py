import logging
from datetime import datetime, timedelta
from models import Account
from database import db

logger = logging.getLogger(__name__)


def record_flood(account):
    """Record a PeerFloodError and compute progressive cooldown.

    Returns the cooldown hours applied.
    """
    now = datetime.utcnow()

    # Reset weekly counter if last flood was > 7 days ago
    if account.last_flood_at and (now - account.last_flood_at).days > 7:
        account.flood_count_week = 0

    account.flood_count_week = (account.flood_count_week or 0) + 1
    account.last_flood_at = now
    account.is_flood_limited = True

    # Progressive cooldown: 1st=24h, 2nd=48h, 3rd=72h, 4th+=96h
    hours = min(24 * account.flood_count_week, 96)
    account.flood_until = now + timedelta(hours=hours)

    logger.warning(
        f"Account {account.phone} flood #{account.flood_count_week} this week — "
        f"cooldown {hours}h until {account.flood_until.strftime('%Y-%m-%d %H:%M')}"
    )
    return hours


def check_flood_expired(account):
    """Check if an account's flood period has expired."""
    if not account.is_flood_limited:
        return True
    if account.flood_until and datetime.utcnow() >= account.flood_until:
        account.is_flood_limited = False
        account.flood_until = None
        db.session.commit()
        return True
    return False


def auto_clear_expired_floods():
    """Clear all accounts whose flood_until has passed. Call before each job."""
    now = datetime.utcnow()
    expired = Account.query.filter(
        Account.is_flood_limited == True,
        Account.flood_until <= now
    ).all()
    for account in expired:
        account.is_flood_limited = False
        account.flood_until = None
        logger.info(f"Auto-cleared flood for account {account.phone}")
    if expired:
        db.session.commit()
    return len(expired)
