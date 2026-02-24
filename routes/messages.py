from flask import Blueprint, render_template, request, jsonify
from database import db
from models import Message, Contact
from services.spintax import spin

messages_bp = Blueprint('messages', __name__)


@messages_bp.route('/api/messages', methods=['GET'])
def list_messages():
    messages = Message.query.order_by(Message.created_at.desc()).all()
    return jsonify([{
        "id": m.id,
        "name": m.name,
        "template_text": m.template_text,
        "is_active": m.is_active,
        "created_at": m.created_at.strftime("%Y-%m-%d %H:%M")
    } for m in messages])


@messages_bp.route('/api/messages', methods=['POST'])
def create_message():
    data = request.json
    name = data.get('name', '').strip()
    template_text = data.get('template_text', '').strip()

    if not name or not template_text:
        return jsonify({"error": "name and template_text are required"}), 400

    msg = Message(name=name, template_text=template_text)
    db.session.add(msg)
    db.session.commit()
    return jsonify({"id": msg.id, "name": msg.name})


@messages_bp.route('/api/messages/<int:msg_id>', methods=['PUT'])
def update_message(msg_id):
    msg = db.session.get(Message, msg_id)
    if not msg:
        return jsonify({"error": "Message not found"}), 404

    data = request.json
    if 'name' in data:
        msg.name = data['name'].strip()
    if 'template_text' in data:
        msg.template_text = data['template_text'].strip()
    if 'is_active' in data:
        msg.is_active = data['is_active']

    db.session.commit()
    return jsonify({"status": "updated"})


@messages_bp.route('/api/messages/<int:msg_id>', methods=['DELETE'])
def delete_message(msg_id):
    msg = db.session.get(Message, msg_id)
    if not msg:
        return jsonify({"error": "Message not found"}), 404
    db.session.delete(msg)
    db.session.commit()
    return jsonify({"status": "deleted"})


@messages_bp.route('/api/messages/<int:msg_id>/preview', methods=['POST'])
def preview_message(msg_id):
    msg = db.session.get(Message, msg_id)
    if not msg:
        return jsonify({"error": "Message not found"}), 404

    sample = Contact.query.first()
    if sample:
        rendered = msg.template_text.replace("{name}", sample.name or "John")
        rendered = rendered.replace("{token}", sample.token or "ABCD1234")
        rendered = rendered.replace("{link}", sample.link or "https://example.com")
    else:
        rendered = msg.template_text.replace("{name}", "John")
        rendered = rendered.replace("{token}", "ABCD1234")
        rendered = rendered.replace("{link}", "https://example.com")

    rendered = spin(rendered)

    return jsonify({"preview": rendered})
