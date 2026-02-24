from flask import Blueprint, render_template, request, jsonify
from database import db
from models import SendHistory, ImportHistory

logs_bp = Blueprint('logs', __name__)


@logs_bp.route('/logs')
def logs_page():
    return render_template('logs.html')


@logs_bp.route('/api/logs/send')
def send_logs():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    status = request.args.get('status', '')

    query = SendHistory.query
    if status:
        query = query.filter_by(status=status)

    query = query.order_by(SendHistory.sent_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    logs = [{
        "id": s.id,
        "phone": s.contact.phone if s.contact else "?",
        "name": s.contact.name if s.contact else "",
        "status": s.status,
        "error_msg": s.error_msg,
        "account": s.account.phone if s.account else "?",
        "message_preview": (s.rendered_message or "")[:100],
        "sent_at": s.sent_at.strftime("%Y-%m-%d %H:%M:%S") if s.sent_at else ""
    } for s in pagination.items]

    return jsonify({
        "logs": logs,
        "total": pagination.total,
        "pages": pagination.pages,
        "page": page
    })


@logs_bp.route('/api/logs/import')
def import_logs():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    status = request.args.get('status', '')

    query = ImportHistory.query
    if status:
        query = query.filter_by(status=status)

    query = query.order_by(ImportHistory.imported_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    logs = [{
        "id": s.id,
        "phone": s.contact.phone if s.contact else "?",
        "status": s.status,
        "tg_name": s.tg_name,
        "tg_username": s.tg_username,
        "error_msg": s.error_msg,
        "account": s.account.phone if s.account else "?",
        "imported_at": s.imported_at.strftime("%Y-%m-%d %H:%M:%S") if s.imported_at else ""
    } for s in pagination.items]

    return jsonify({
        "logs": logs,
        "total": pagination.total,
        "pages": pagination.pages,
        "page": page
    })
