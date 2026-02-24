from datetime import datetime
from database import db


class Account(db.Model):
    __tablename__ = 'accounts'

    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    api_id = db.Column(db.Integer, nullable=False)
    api_hash = db.Column(db.String(64), nullable=False)
    session_name = db.Column(db.String(100), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    is_connected = db.Column(db.Boolean, default=False)
    is_flood_limited = db.Column(db.Boolean, default=False)
    flood_until = db.Column(db.DateTime, nullable=True)
    flood_count_week = db.Column(db.Integer, default=0)
    last_flood_at = db.Column(db.DateTime, nullable=True)
    daily_limit = db.Column(db.Integer, default=20)
    warmup_start_date = db.Column(db.DateTime, nullable=True)
    display_name = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    send_history = db.relationship('SendHistory', backref='account', lazy='dynamic', cascade='all, delete-orphan')
    import_history = db.relationship('ImportHistory', backref='account', lazy='dynamic', cascade='all, delete-orphan')
    chat_messages = db.relationship('ChatMessage', backref='account', lazy='dynamic', cascade='all, delete-orphan')


class Contact(db.Model):
    __tablename__ = 'contacts'

    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=True)
    token = db.Column(db.String(50), nullable=True)
    link = db.Column(db.String(255), nullable=True)

    telegram_status = db.Column(db.String(20), default='unknown')
    tg_name = db.Column(db.String(100), nullable=True)
    tg_username = db.Column(db.String(100), nullable=True)
    tg_user_id = db.Column(db.BigInteger, nullable=True)
    tg_last_seen = db.Column(db.String(20), default='unknown')  # online, recently, last_week, last_month, long_ago, unknown

    last_send_status = db.Column(db.String(20), default='pending')
    last_send_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    send_history = db.relationship('SendHistory', backref='contact', lazy='dynamic', cascade='all, delete-orphan')
    import_history = db.relationship('ImportHistory', backref='contact', lazy='dynamic', cascade='all, delete-orphan')
    chat_messages = db.relationship('ChatMessage', backref='contact', lazy='dynamic', cascade='all, delete-orphan')


class Message(db.Model):
    __tablename__ = 'messages'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    template_text = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    send_history = db.relationship('SendHistory', backref='message', lazy='dynamic', cascade='all, delete-orphan')


class SendHistory(db.Model):
    __tablename__ = 'send_history'

    id = db.Column(db.Integer, primary_key=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('contacts.id'), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'), nullable=False, index=True)
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id'), nullable=True)
    status = db.Column(db.String(20), nullable=False)
    error_msg = db.Column(db.Text, nullable=True)
    rendered_message = db.Column(db.Text, nullable=True)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class ImportHistory(db.Model):
    __tablename__ = 'import_history'

    id = db.Column(db.Integer, primary_key=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('contacts.id'), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False)
    tg_name = db.Column(db.String(100), nullable=True)
    tg_username = db.Column(db.String(100), nullable=True)
    error_msg = db.Column(db.Text, nullable=True)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id'), nullable=False, index=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('contacts.id'), nullable=True, index=True)
    tg_user_id = db.Column(db.BigInteger, nullable=False, index=True)
    tg_name = db.Column(db.String(100), nullable=True)
    tg_username = db.Column(db.String(100), nullable=True)
    direction = db.Column(db.String(10), nullable=False)  # 'incoming' or 'outgoing'
    text = db.Column(db.Text, nullable=False)
    tg_message_id = db.Column(db.Integer, nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    delivery_status = db.Column(db.String(10), default='sent')  # sent, delivered, read (outgoing only)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
