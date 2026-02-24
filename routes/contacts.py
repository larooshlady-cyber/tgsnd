import csv
import io
from flask import Blueprint, render_template, request, jsonify, Response
from database import db
from models import Contact, ChatMessage
from services.csv_service import parse_and_import_csv

contacts_bp = Blueprint('contacts', __name__)


@contacts_bp.route('/contacts')
def contacts_page():
    return render_template('contacts.html')


@contacts_bp.route('/api/contacts', methods=['GET'])
def list_contacts():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    search = request.args.get('search', '').strip()
    tg_status = request.args.get('telegram_status', '')
    send_status = request.args.get('send_status', '')
    last_seen = request.args.get('last_seen', '')
    exclude_messaged = request.args.get('exclude_messaged', '')

    query = Contact.query

    if search:
        query = query.filter(
            db.or_(
                Contact.phone.ilike(f'%{search}%'),
                Contact.name.ilike(f'%{search}%'),
                Contact.tg_username.ilike(f'%{search}%')
            )
        )
    if tg_status:
        query = query.filter_by(telegram_status=tg_status)
    if send_status:
        query = query.filter_by(last_send_status=send_status)
    if last_seen == 'active':
        query = query.filter(Contact.tg_last_seen.in_(['online', 'recently']))
    elif last_seen == 'inactive':
        query = query.filter(Contact.tg_last_seen.in_(['last_month', 'long_ago']))
    elif last_seen:
        query = query.filter_by(tg_last_seen=last_seen)
    if exclude_messaged == '1':
        messaged_ids = db.session.query(ChatMessage.contact_id).filter(
            ChatMessage.direction == 'outgoing',
            ChatMessage.contact_id.isnot(None)
        ).distinct().subquery()
        query = query.filter(~Contact.id.in_(db.session.query(messaged_ids)))

    query = query.order_by(Contact.id.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    contacts = [{
        "id": c.id,
        "phone": c.phone,
        "name": c.name,
        "token": c.token,
        "link": c.link,
        "telegram_status": c.telegram_status,
        "tg_name": c.tg_name,
        "tg_username": c.tg_username,
        "tg_user_id": c.tg_user_id,
        "tg_last_seen": c.tg_last_seen,
        "last_send_status": c.last_send_status,
        "last_send_at": c.last_send_at.strftime("%Y-%m-%d %H:%M") if c.last_send_at else None
    } for c in pagination.items]

    return jsonify({
        "contacts": contacts,
        "total": pagination.total,
        "pages": pagination.pages,
        "page": page,
        "per_page": per_page
    })


@contacts_bp.route('/api/contacts', methods=['POST'])
def create_contact():
    data = request.json
    phone = (data.get('phone') or '').strip()
    if not phone:
        return jsonify({"error": "Phone number is required"}), 400

    # Normalize: ensure starts with +
    if not phone.startswith('+'):
        phone = '+' + phone

    existing = Contact.query.filter_by(phone=phone).first()
    if existing:
        return jsonify({"error": f"Contact with phone {phone} already exists"}), 409

    contact = Contact(
        phone=phone,
        name=(data.get('name') or '').strip() or None,
        token=(data.get('token') or '').strip() or None,
        link=(data.get('link') or '').strip() or None
    )
    db.session.add(contact)
    db.session.commit()

    return jsonify({
        "id": contact.id,
        "phone": contact.phone,
        "name": contact.name,
        "token": contact.token,
        "link": contact.link
    }), 201


@contacts_bp.route('/api/contacts/upload', methods=['POST'])
def upload_contacts():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if not file.filename.endswith('.csv'):
        return jsonify({"error": "File must be a CSV"}), 400

    stats = parse_and_import_csv(file)
    return jsonify(stats)


@contacts_bp.route('/api/contacts/<int:contact_id>', methods=['DELETE'])
def delete_contact(contact_id):
    contact = db.session.get(Contact, contact_id)
    if not contact:
        return jsonify({"error": "Contact not found"}), 404
    db.session.delete(contact)
    db.session.commit()
    return jsonify({"status": "deleted"})


@contacts_bp.route('/api/contacts/bulk-delete', methods=['POST'])
def bulk_delete():
    data = request.json
    ids = data.get('ids', [])
    if not ids:
        return jsonify({"error": "No IDs provided"}), 400
    # Use ORM delete to trigger cascade (bulk .delete() bypasses it)
    contacts = Contact.query.filter(Contact.id.in_(ids)).all()
    for c in contacts:
        db.session.delete(c)
    db.session.commit()
    return jsonify({"deleted": len(contacts)})


@contacts_bp.route('/api/contacts/unscanned-ids')
def unscanned_ids():
    """Return IDs of contacts that haven't been scanned yet (telegram_status=unknown)."""
    ids = db.session.query(Contact.id).filter_by(telegram_status='unknown').all()
    return jsonify({"ids": [r[0] for r in ids], "count": len(ids)})


@contacts_bp.route('/api/contacts/scanned-ids')
def scanned_ids():
    """Return IDs of contacts already scanned (on_telegram) for rescanning."""
    ids = db.session.query(Contact.id).filter_by(telegram_status='on_telegram').all()
    return jsonify({"ids": [r[0] for r in ids], "count": len(ids)})


@contacts_bp.route('/api/contacts/export')
def export_contacts():
    contacts = Contact.query.all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["phone", "name", "token", "link", "telegram_status", "tg_user_id", "tg_name", "tg_username", "tg_last_seen", "last_send_status"])
    for c in contacts:
        writer.writerow([c.phone, c.name, c.token, c.link, c.telegram_status, c.tg_user_id, c.tg_name, c.tg_username, c.tg_last_seen, c.last_send_status])

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={"Content-Disposition": "attachment; filename=contacts_export.csv"}
    )
