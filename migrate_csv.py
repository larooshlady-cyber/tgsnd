"""
One-time migration: import existing CSV data into SQLite database.
Run: python migrate_csv.py
"""
import csv
import os
import shutil
from datetime import datetime
from app import create_app
from database import db
from models import Account, Contact, ImportHistory, SendHistory, Message

app = create_app()

# Existing account credentials from telegram_sender.py
EXISTING_API_ID = 37694913
EXISTING_API_HASH = "c2affca91b51dc1a98f60f76a28ea68a"
EXISTING_PHONE = "+18624153495"
EXISTING_SESSION = "session1222"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def migrate():
    with app.app_context():
        print("=== Migration Start ===\n")

        # 1. Move existing session file to sessions/ dir
        src = os.path.join(BASE_DIR, f"{EXISTING_SESSION}.session")
        dst = os.path.join(BASE_DIR, "sessions", f"{EXISTING_SESSION}.session")
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            print(f"Copied session file to sessions/{EXISTING_SESSION}.session")

        # 2. Create account for existing session
        existing = Account.query.filter_by(phone=EXISTING_PHONE).first()
        if not existing:
            account = Account(
                phone=EXISTING_PHONE,
                api_id=EXISTING_API_ID,
                api_hash=EXISTING_API_HASH,
                session_name=EXISTING_SESSION
            )
            db.session.add(account)
            db.session.commit()
            print(f"Created account: {EXISTING_PHONE}")
        else:
            account = existing
            print(f"Account already exists: {EXISTING_PHONE}")

        # 3. Import contacts.csv
        contacts_file = os.path.join(BASE_DIR, "contacts.csv")
        if os.path.exists(contacts_file):
            created = 0
            with open(contacts_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    phone = row.get("phone", "").strip().replace(" ", "").replace("-", "")
                    if not phone:
                        continue
                    if not phone.startswith("+"):
                        phone = "+" + phone

                    if not Contact.query.filter_by(phone=phone).first():
                        c = Contact(
                            phone=phone,
                            name=row.get("name", "").strip(),
                            token=row.get("token", "").strip(),
                            link=row.get("link", "").strip()
                        )
                        db.session.add(c)
                        created += 1

            db.session.commit()
            print(f"Imported {created} contacts from contacts.csv")
        else:
            print("contacts.csv not found, skipping")

        # 4. Import import_log.csv -> update contact statuses
        import_log = os.path.join(BASE_DIR, "import_log.csv")
        if os.path.exists(import_log):
            updated = 0
            with open(import_log, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    phone = row.get("phone", "").strip()
                    status = row.get("status", "").strip()
                    tg_name = row.get("tg_name", "").strip()
                    username = row.get("username", "").strip()

                    contact = Contact.query.filter_by(phone=phone).first()
                    if contact:
                        if status in ("IMPORTED", "ADDED"):
                            contact.telegram_status = "on_telegram"
                            contact.tg_name = tg_name or contact.tg_name
                            contact.tg_username = username or contact.tg_username
                        elif status == "NOT_FOUND":
                            contact.telegram_status = "not_found"
                        updated += 1

            db.session.commit()
            print(f"Updated {updated} contacts from import_log.csv")
        else:
            print("import_log.csv not found, skipping")

        # 5. Import send_log.csv -> update contact send statuses
        send_log = os.path.join(BASE_DIR, "send_log.csv")
        if os.path.exists(send_log):
            updated = 0
            with open(send_log, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    phone = row.get("phone", "").strip()
                    status = row.get("status", "").strip()

                    contact = Contact.query.filter_by(phone=phone).first()
                    if contact:
                        if status == "SENT":
                            contact.last_send_status = "sent"
                        elif status == "PEER_FLOOD":
                            contact.last_send_status = "peer_flood"
                        elif status == "NOT_ON_TELEGRAM":
                            contact.last_send_status = "not_on_telegram"
                        updated += 1

            db.session.commit()
            print(f"Updated {updated} contacts from send_log.csv")
        else:
            print("send_log.csv not found, skipping")

        print("\n=== Migration Complete ===")


if __name__ == "__main__":
    migrate()
