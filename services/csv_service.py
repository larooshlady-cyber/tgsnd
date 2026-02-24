import csv
import io
from models import Contact
from database import db


def parse_and_import_csv(file_storage):
    """Parse uploaded CSV and upsert contacts into database."""
    stream = io.StringIO(file_storage.stream.read().decode('utf-8'))
    reader = csv.DictReader(stream)

    stats = {"created": 0, "updated": 0, "skipped": 0, "errors": []}

    for row_num, row in enumerate(reader, start=2):
        phone = row.get("phone", "").strip().replace(" ", "").replace("-", "")
        if not phone:
            stats["skipped"] += 1
            continue
        if not phone.startswith("+"):
            phone = "+" + phone

        name = row.get("name", "").strip()
        token = row.get("token", "").strip()
        link = row.get("link", "").strip()

        existing = Contact.query.filter_by(phone=phone).first()
        if existing:
            existing.name = name or existing.name
            existing.token = token or existing.token
            existing.link = link or existing.link
            stats["updated"] += 1
        else:
            contact = Contact(phone=phone, name=name, token=token, link=link)
            db.session.add(contact)
            stats["created"] += 1

    db.session.commit()
    return stats
