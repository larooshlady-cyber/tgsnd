import os
import logging
from flask import Flask
from database import db
from services.telegram_service import telegram_manager

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')


def create_app():
    app = Flask(__name__)
    app.config.from_object('config')

    os.makedirs(app.config['SESSIONS_DIR'], exist_ok=True)
    os.makedirs(os.path.join(os.path.dirname(__file__), 'data'), exist_ok=True)

    db.init_app(app)

    with app.app_context():
        import models  # noqa: F401
        db.create_all()

        # Migrate: add missing columns
        from sqlalchemy import inspect, text
        insp = inspect(db.engine)
        try:
            cols = [c['name'] for c in insp.get_columns('chat_messages')]
            if 'delivery_status' not in cols:
                db.session.execute(text("ALTER TABLE chat_messages ADD COLUMN delivery_status VARCHAR(10) DEFAULT 'sent'"))
                db.session.commit()
        except Exception:
            pass

        try:
            acct_cols = [c['name'] for c in insp.get_columns('accounts')]
            for col_name, col_def in [
                ('daily_limit', "INTEGER DEFAULT 20"),
                ('warmup_start_date', "DATETIME"),
                ('flood_count_week', "INTEGER DEFAULT 0"),
                ('last_flood_at', "DATETIME"),
            ]:
                if col_name not in acct_cols:
                    db.session.execute(text(f"ALTER TABLE accounts ADD COLUMN {col_name} {col_def}"))
                    db.session.commit()
        except Exception:
            pass

    from routes.dashboard import dashboard_bp
    from routes.accounts import accounts_bp
    from routes.contacts import contacts_bp
    from routes.messages import messages_bp
    from routes.sender import sender_bp
    from routes.importer import importer_bp
    from routes.logs import logs_bp
    from routes.chats import chats_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(contacts_bp)
    app.register_blueprint(messages_bp)
    app.register_blueprint(sender_bp)
    app.register_blueprint(importer_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(chats_bp)

    telegram_manager.start()
    telegram_manager.app = app

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5001, threaded=True, use_reloader=False)
