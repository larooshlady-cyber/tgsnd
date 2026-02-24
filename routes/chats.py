import json
import queue
import asyncio
from flask import Blueprint, render_template, request, jsonify, Response
from database import db
from models import ChatMessage, Contact
from services.chat_service import subscribe_sse, unsubscribe_sse, send_chat_message, check_read_status
from services.telegram_service import telegram_manager

chats_bp = Blueprint('chats', __name__)


@chats_bp.route('/chats')
def chats_page():
    return render_template('chats.html')


@chats_bp.route('/api/chats')
def list_conversations():
    """List conversations grouped by tg_user_id with last message and unread count."""
    subq_last = (
        db.session.query(
            ChatMessage.tg_user_id,
            db.func.max(ChatMessage.id).label('last_id')
        )
        .group_by(ChatMessage.tg_user_id)
        .subquery()
    )

    rows = (
        db.session.query(ChatMessage)
        .join(subq_last, ChatMessage.id == subq_last.c.last_id)
        .order_by(ChatMessage.created_at.desc())
        .all()
    )

    if not rows:
        return jsonify([])

    # Batch: get all tg_user_ids
    user_ids = [msg.tg_user_id for msg in rows]

    # Batch: unread counts per user
    unread_q = (
        db.session.query(ChatMessage.tg_user_id, db.func.count(ChatMessage.id))
        .filter(ChatMessage.tg_user_id.in_(user_ids), ChatMessage.direction == 'incoming', ChatMessage.is_read == False)
        .group_by(ChatMessage.tg_user_id)
        .all()
    )
    unread_map = dict(unread_q)

    # Batch: has_reply (any incoming message exists)
    reply_q = (
        db.session.query(ChatMessage.tg_user_id)
        .filter(ChatMessage.tg_user_id.in_(user_ids), ChatMessage.direction == 'incoming')
        .distinct()
        .all()
    )
    reply_set = {r[0] for r in reply_q}

    # Batch: read count per user (outgoing messages with read status)
    read_q = (
        db.session.query(ChatMessage.tg_user_id, db.func.count(ChatMessage.id))
        .filter(ChatMessage.tg_user_id.in_(user_ids), ChatMessage.direction == 'outgoing', ChatMessage.delivery_status == 'read')
        .group_by(ChatMessage.tg_user_id)
        .all()
    )
    read_map = dict(read_q)

    # Batch: contact phones
    contact_ids = [msg.contact_id for msg in rows if msg.contact_id]
    contact_phone_map = {}
    if contact_ids:
        contacts = Contact.query.filter(Contact.id.in_(contact_ids)).all()
        contact_phone_map = {c.id: c.phone or "" for c in contacts}

    conversations = []
    for msg in rows:
        conversations.append({
            "tg_user_id": msg.tg_user_id,
            "tg_name": msg.tg_name or "",
            "tg_username": msg.tg_username or "",
            "contact_phone": contact_phone_map.get(msg.contact_id, ""),
            "account_id": msg.account_id,
            "contact_id": msg.contact_id,
            "last_message": msg.text[:80] if msg.text else "",
            "last_direction": msg.direction,
            "last_time": msg.created_at.strftime("%Y-%m-%d %H:%M"),
            "unread": unread_map.get(msg.tg_user_id, 0),
            "has_reply": msg.tg_user_id in reply_set,
            "read_count": read_map.get(msg.tg_user_id, 0)
        })

    return jsonify(conversations)


@chats_bp.route('/api/chats/<int:tg_user_id>/messages')
def get_messages(tg_user_id):
    """Get paginated messages for a conversation."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)

    pagination = (
        ChatMessage.query
        .filter_by(tg_user_id=tg_user_id)
        .order_by(ChatMessage.created_at.asc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    messages = [{
        "id": m.id,
        "account_id": m.account_id,
        "direction": m.direction,
        "text": m.text,
        "is_read": m.is_read,
        "delivery_status": m.delivery_status or "sent",
        "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "tg_message_id": m.tg_message_id
    } for m in pagination.items]

    return jsonify({
        "messages": messages,
        "total": pagination.total,
        "pages": pagination.pages,
        "page": page
    })


@chats_bp.route('/api/chats/<int:tg_user_id>/send', methods=['POST'])
def send_message(tg_user_id):
    """Send a reply message."""
    data = request.json
    text = data.get('text', '').strip()
    if not text:
        return jsonify({"error": "Text is required"}), 400

    # Find the account used in the last conversation with this user
    last_msg = (
        ChatMessage.query
        .filter_by(tg_user_id=tg_user_id)
        .order_by(ChatMessage.created_at.desc())
        .first()
    )
    if not last_msg:
        return jsonify({"error": "No conversation found"}), 404

    account_id = last_msg.account_id

    try:
        result = telegram_manager.run_async(
            send_chat_message(account_id, tg_user_id, text)
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@chats_bp.route('/api/chats/<int:tg_user_id>/read', methods=['POST'])
def mark_read(tg_user_id):
    """Mark all incoming messages from this user as read."""
    ChatMessage.query.filter_by(
        tg_user_id=tg_user_id,
        direction='incoming',
        is_read=False
    ).update({"is_read": True})
    db.session.commit()
    return jsonify({"status": "ok"})


@chats_bp.route('/api/chats/unread-count')
def unread_count():
    """Total unread incoming messages."""
    count = ChatMessage.query.filter_by(direction='incoming', is_read=False).count()
    return jsonify({"count": count})


@chats_bp.route('/api/chats/check-status', methods=['POST'])
def check_status():
    """Actively check read/delivery status from Telegram for all conversations."""
    try:
        updated = telegram_manager.run_async(check_read_status())
        return jsonify({"updated": updated})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@chats_bp.route('/api/chats/stream')
def sse_stream():
    """SSE stream for real-time chat updates."""
    q = subscribe_sse()

    def generate():
        try:
            while True:
                try:
                    data = q.get(timeout=15)
                    if data is None:
                        break
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
                    # Send keepalive comment to prevent timeout
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            unsubscribe_sse(q)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
