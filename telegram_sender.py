"""
Telegram Contact Importer — One by One
========================================
1. Uses ImportContactsRequest to FIND users on Telegram
2. Uses AddContactRequest to actually ADD them as contacts
"""

import asyncio
import random
import csv
from datetime import datetime
from telethon import TelegramClient
from telethon.tl.functions.contacts import ImportContactsRequest, AddContactRequest
from telethon.tl.types import InputPhoneContact, InputUser

# ===================== CONFIG =====================
API_ID = 37694913
API_HASH = "c2affca91b51dc1a98f60f76a28ea68a"
PHONE_NUMBER = "+18624153495"
SESSION_NAME = "session1222"

DELAY_MIN = 5
DELAY_MAX = 10
LOG_FILE = "import_log.csv"
# ==================================================


def load_done(log_file=LOG_FILE):
    done = set()
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                done.add(row.get("phone", "").strip())
        if done:
            print(f"📋 {len(done)} already scanned — will skip them")
    except FileNotFoundError:
        pass
    return done


def load_contacts(filename="contacts.csv"):
    contacts = []
    with open(filename, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            phone = row.get("phone", "").strip().replace(" ", "").replace("-", "")
            if not phone:
                continue
            if not phone.startswith("+"):
                phone = "+" + phone
            contacts.append(phone)
    print(f"✅ Loaded {len(contacts)} contacts")
    return contacts


def log_status(phone, status, tg_name="", username=""):
    file_exists = False
    try:
        with open(LOG_FILE, "r"):
            file_exists = True
    except FileNotFoundError:
        pass

    with open(LOG_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "phone", "status", "tg_name", "username"])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            phone, status, tg_name, username
        ])


async def import_contacts():
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start(phone=PHONE_NUMBER)

    me = await client.get_me()
    print(f"✅ Logged in as {me.first_name} ({PHONE_NUMBER})\n")

    phones = load_contacts()
    done = load_done()

    phones = [p for p in phones if p not in done]
    print(f"📥 {len(phones)} contacts to scan\n")
    print("=" * 50)

    if not phones:
        print("✅ All contacts already scanned!")
        await client.disconnect()
        return

    found = 0
    not_found = 0

    for i, phone in enumerate(phones, 1):
        try:
            # Step 1: Find the user via ImportContactsRequest
            result = await client(ImportContactsRequest([
                InputPhoneContact(
                    client_id=random.randint(0, 9999999),
                    phone=phone,
                    first_name="Contact",
                    last_name=""
                )
            ]))

            if result.users:
                user = result.users[0]
                tg_name = (user.first_name or "") + (" " + user.last_name if user.last_name else "")
                tg_name = tg_name.strip()
                username = user.username or ""

                # Step 2: Actually ADD as a contact using AddContactRequest
                await client(AddContactRequest(
                    id=InputUser(user_id=user.id, access_hash=user.access_hash),
                    first_name=user.first_name or "Contact",
                    last_name=user.last_name or "",
                    phone=phone,
                    add_phone_privacy_exception=True
                ))

                found += 1
                print(f"✅ [{i}/{len(phones)}] {phone} — {tg_name} (@{username or 'no username'}) — ADDED ✔️")
                log_status(phone, "ADDED", tg_name, username)
            else:
                not_found += 1
                print(f"❌ [{i}/{len(phones)}] {phone} — not on Telegram")
                log_status(phone, "NOT_FOUND")

        except Exception as e:
            print(f"⚠️  [{i}/{len(phones)}] {phone} — error: {e}")
            log_status(phone, f"ERROR: {e}")

        # Delay
        if i < len(phones):
            delay = random.uniform(DELAY_MIN, DELAY_MAX)
            print(f"   ⏳ {delay:.0f}s...\n")
            await asyncio.sleep(delay)

    print(f"\n{'=' * 50}")
    print(f"📊 DONE:")
    print(f"   ✅ Added to contacts: {found}")
    print(f"   ❌ Not on Telegram: {not_found}")
    print(f"📝 Log: {LOG_FILE}")
    print(f"{'=' * 50}")

    await client.disconnect()


if __name__ == "__main__":
    print("=" * 50)
    print("  TELEGRAM CONTACT IMPORTER")
    print("=" * 50)
    asyncio.run(import_contacts())