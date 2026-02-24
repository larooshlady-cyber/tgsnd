from flask import Blueprint, render_template, jsonify
from sqlalchemy import func
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
