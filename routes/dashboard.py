from flask import Blueprint, render_template, jsonify, request
from sqlalchemy import func, case
from datetime import datetime, timedelta
from database import db
from models import Contact, Account, SendHistory, ImportHistory

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
def index():
    return render_template('dashboard.html')


@dashboard_bp.route('/api/stats')
def stats():
    total_contacts = Contact.query.count()
    on_telegram = Contact.query.filter_by(telegram_status='on_telegram').count()
    not_found = Contact.query.filter_by(telegram_status='not_found').count()
    unknown = Contact.query.filter_by(telegram_status='unknown').count()

    total_sent = SendHistory.query.filter_by(status='sent').count()
    total_failed = SendHistory.query.filter(SendHistory.status.in_(['failed', 'peer_flood', 'not_on_telegram'])).count()
    total_peer_flood = SendHistory.query.filter_by(status='peer_flood').count()

    pending = Contact.query.filter_by(last_send_status='pending').count()
    sent = Contact.query.filter_by(last_send_status='sent').count()

    accounts_total = Account.query.count()
    accounts_connected = Account.query.filter_by(is_connected=True).count()
    accounts_flood = Account.query.filter_by(is_flood_limited=True).count()

    recent_sends = db.session.query(SendHistory).order_by(SendHistory.sent_at.desc()).limit(10).all()
    recent = [{
        "id": s.id,
        "phone": s.contact.phone if s.contact else "?",
        "name": s.contact.name if s.contact else "",
        "status": s.status,
        "account": s.account.phone if s.account else "?",
        "sent_at": s.sent_at.strftime("%Y-%m-%d %H:%M:%S") if s.sent_at else ""
    } for s in recent_sends]

    return jsonify({
        "contacts": {
            "total": total_contacts,
            "on_telegram": on_telegram,
            "not_found": not_found,
            "unknown": unknown
        },
        "sending": {
            "total_sent": total_sent,
            "total_failed": total_failed,
            "total_peer_flood": total_peer_flood,
            "pending": pending,
            "sent_contacts": sent
        },
        "accounts": {
            "total": accounts_total,
            "connected": accounts_connected,
            "flood_limited": accounts_flood
        },
        "recent_sends": recent
    })


@dashboard_bp.route('/api/stats/daily-sends')
def daily_sends():
    days = request.args.get('days', 14, type=int)
    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = db.session.query(
        func.date(SendHistory.sent_at).label('day'),
        func.sum(case((SendHistory.status == 'sent', 1), else_=0)).label('sent'),
        func.sum(case((SendHistory.status == 'failed', 1), else_=0)).label('failed'),
        func.sum(case((SendHistory.status == 'peer_flood', 1), else_=0)).label('flood'),
    ).filter(
        SendHistory.sent_at >= cutoff
    ).group_by(
        func.date(SendHistory.sent_at)
    ).order_by(
        func.date(SendHistory.sent_at)
    ).all()

    return jsonify([{
        'day': str(r.day),
        'sent': r.sent or 0,
        'failed': r.failed or 0,
        'flood': r.flood or 0
    } for r in rows])


@dashboard_bp.route('/api/stats/hourly-sends')
def hourly_sends():
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    rows = db.session.query(
        func.strftime('%H', SendHistory.sent_at).label('hour'),
        func.sum(case((SendHistory.status == 'sent', 1), else_=0)).label('sent'),
        func.sum(case(
            (SendHistory.status.in_(['failed', 'peer_flood', 'not_on_telegram']), 1),
            else_=0
        )).label('errors'),
    ).filter(
        SendHistory.sent_at >= today_start
    ).group_by(
        func.strftime('%H', SendHistory.sent_at)
    ).order_by(
        func.strftime('%H', SendHistory.sent_at)
    ).all()

    hour_map = {int(r.hour): {'sent': r.sent or 0, 'errors': r.errors or 0} for r in rows}
    result = []
    for h in range(24):
        result.append({
            'hour': f'{h:02d}:00',
            'sent': hour_map.get(h, {}).get('sent', 0),
            'errors': hour_map.get(h, {}).get('errors', 0)
        })
    return jsonify(result)


@dashboard_bp.route('/api/stats/account-usage')
def account_usage():
    from services.send_service import get_remaining_capacity, _get_effective_limit
    accounts = Account.query.filter_by(is_active=True).all()
    result = []
    for a in accounts:
        effective_limit = _get_effective_limit(a)
        remaining = get_remaining_capacity(a.id)
        used = max(0, effective_limit - remaining)
        result.append({
            'id': a.id,
            'label': a.display_name or a.phone,
            'phone': a.phone,
            'used': used,
            'remaining': remaining,
            'limit': effective_limit,
            'is_flood_limited': a.is_flood_limited,
            'is_connected': a.is_connected
        })
    return jsonify(result)
