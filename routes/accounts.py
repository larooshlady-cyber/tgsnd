import csv
import io
import os
import asyncio
import shutil
import tempfile
import zipfile
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, current_app
from database import db
from models import Account
from services.telegram_service import telegram_manager
from services.send_service import get_remaining_capacity, _get_effective_limit

accounts_bp = Blueprint('accounts', __name__)


@accounts_bp.route('/accounts')
def accounts_page():
    return render_template('accounts.html')


@accounts_bp.route('/api/accounts', methods=['GET'])
def list_accounts():
    accounts = Account.query.order_by(Account.created_at.desc()).all()
    result = []
    for a in accounts:
        connected = telegram_manager.is_connected(a.id)
        a.is_connected = connected
        effective_limit = _get_effective_limit(a)
        remaining = get_remaining_capacity(a.id)
        warmup_day = None
        if a.warmup_start_date:
            warmup_day = (datetime.utcnow() - a.warmup_start_date).days

        result.append({
            "id": a.id,
            "phone": a.phone,
            "api_id": a.api_id,
            "session_name": a.session_name,
            "is_active": a.is_active,
            "is_connected": connected,
            "is_flood_limited": a.is_flood_limited,
            "flood_until": a.flood_until.strftime("%Y-%m-%d %H:%M") if a.flood_until else None,
            "flood_count_week": a.flood_count_week or 0,
            "daily_limit": a.daily_limit or 20,
            "effective_limit": effective_limit,
            "remaining_today": remaining,
            "warmup_day": warmup_day,
            "warmup_done": warmup_day is None or warmup_day >= 4,
            "display_name": a.display_name,
            "created_at": a.created_at.strftime("%Y-%m-%d %H:%M")
        })
    db.session.commit()
    return jsonify(result)


@accounts_bp.route('/api/accounts', methods=['POST'])
def add_account():
    data = request.json
    phone = data.get('phone', '').strip()
    api_id = data.get('api_id')
    api_hash = data.get('api_hash', '').strip()

    if not phone or not api_id or not api_hash:
        return jsonify({"error": "phone, api_id, api_hash are required"}), 400

    if not phone.startswith('+'):
        phone = '+' + phone

    session_name = "session_" + phone.replace('+', '').replace(' ', '')

    existing = Account.query.filter_by(phone=phone).first()
    if existing:
        return jsonify({"error": "Account with this phone already exists"}), 400

    account = Account(
        phone=phone,
        api_id=int(api_id),
        api_hash=api_hash,
        session_name=session_name,
        warmup_start_date=datetime.utcnow()
    )
    db.session.add(account)
    db.session.commit()

    return jsonify({"id": account.id, "phone": account.phone, "session_name": session_name})


@accounts_bp.route('/api/accounts/bulk-upload', methods=['POST'])
def bulk_upload_accounts():
    """Bulk import accounts from CSV. Format: phone,api_id,api_hash"""
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if not file.filename.endswith('.csv'):
        return jsonify({"error": "File must be a CSV"}), 400

    stream = io.StringIO(file.stream.read().decode('utf-8'))
    reader = csv.DictReader(stream)

    created = 0
    skipped = 0
    errors = []

    for row_num, row in enumerate(reader, start=2):
        phone = row.get('phone', '').strip().replace(' ', '').replace('-', '')
        api_id = row.get('api_id', '').strip()
        api_hash = row.get('api_hash', '').strip()

        if not phone or not api_id or not api_hash:
            errors.append(f"Row {row_num}: missing fields")
            continue

        if not phone.startswith('+'):
            phone = '+' + phone

        try:
            api_id = int(api_id)
        except ValueError:
            errors.append(f"Row {row_num}: invalid api_id")
            continue

        existing = Account.query.filter_by(phone=phone).first()
        if existing:
            skipped += 1
            continue

        session_name = "session_" + phone.replace('+', '').replace(' ', '')
        account = Account(
            phone=phone,
            api_id=api_id,
            api_hash=api_hash,
            session_name=session_name
        )
        db.session.add(account)
        created += 1

    db.session.commit()
    return jsonify({"created": created, "skipped": skipped, "errors": errors})


@accounts_bp.route('/api/accounts/import-tdata', methods=['POST'])
def import_tdata():
    """
    Import accounts from tdata folders.
    Accepts a ZIP file where each top-level folder is named by phone number
    and contains a 'tdata' subfolder. Example ZIP structure:
      +18624153495/tdata/key_datas
      +18624153495/tdata/D877F783D5D3EF8C/...
      +19876543210/tdata/key_datas
      ...
    Or a single tdata folder directly (phone extracted after conversion).
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if not file.filename.endswith('.zip'):
        return jsonify({"error": "File must be a ZIP containing tdata folder(s)"}), 400

    sessions_dir = current_app.config['SESSIONS_DIR']
    results = []
    tmp_dir = tempfile.mkdtemp(prefix='tdata_import_')

    try:
        # Extract ZIP
        zip_path = os.path.join(tmp_dir, 'upload.zip')
        file.save(zip_path)

        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(tmp_dir)

        os.remove(zip_path)

        # Find all tdata directories in the extracted content
        tdata_dirs = _find_tdata_dirs(tmp_dir)

        if not tdata_dirs:
            return jsonify({"error": "No tdata folders found in ZIP. Expected structure: phone_number/tdata/ or just tdata/"}), 400

        from opentele.td import TDesktop
        from opentele.api import UseCurrentSession

        for phone_hint, tdata_path in tdata_dirs:
            try:
                tdesk = TDesktop(tdata_path)
                if tdesk.accountsCount == 0:
                    results.append({"phone": phone_hint or "unknown", "status": "error", "error": "No accounts in tdata"})
                    continue

                for acc_idx in range(tdesk.accountsCount):
                    # Generate a session name
                    session_name = f"tdata_{phone_hint.replace('+', '').replace(' ', '') if phone_hint else 'unknown'}_{acc_idx}"
                    session_path = os.path.join(sessions_dir, session_name)

                    # Convert tdata to Telethon session
                    client = asyncio.run_coroutine_threadsafe(
                        tdesk.ToTelethon(
                            session=session_path,
                            flag=UseCurrentSession
                        ),
                        telegram_manager._loop
                    ).result(timeout=30)

                    # Connect to get phone number and name
                    connect_result = asyncio.run_coroutine_threadsafe(
                        _connect_and_get_info(client),
                        telegram_manager._loop
                    ).result(timeout=30)

                    actual_phone = connect_result.get("phone", phone_hint or "unknown")
                    display_name = connect_result.get("display_name", "")
                    api_id = connect_result.get("api_id", 0)
                    api_hash = connect_result.get("api_hash", "")

                    if not actual_phone.startswith('+'):
                        actual_phone = '+' + actual_phone

                    # Rename session file if phone was unknown
                    final_session_name = "session_" + actual_phone.replace('+', '').replace(' ', '')
                    final_session_path = os.path.join(sessions_dir, final_session_name)
                    if session_path + '.session' != final_session_path + '.session':
                        if os.path.exists(session_path + '.session'):
                            shutil.move(session_path + '.session', final_session_path + '.session')

                    # Check if account already exists
                    existing = Account.query.filter_by(phone=actual_phone).first()
                    if existing:
                        results.append({"phone": actual_phone, "status": "skipped", "error": "Already exists"})
                        # Disconnect client
                        asyncio.run_coroutine_threadsafe(client.disconnect(), telegram_manager._loop).result(timeout=10)
                        continue

                    # Create account in DB
                    account = Account(
                        phone=actual_phone,
                        api_id=api_id or 2040,
                        api_hash=api_hash or "b18441a1ff607e10a989891a5462e627",
                        session_name=final_session_name,
                        display_name=display_name,
                        is_connected=True,
                        warmup_start_date=datetime.utcnow()
                    )
                    db.session.add(account)
                    db.session.flush()

                    # Store client in manager
                    telegram_manager._clients[account.id] = client
                    telegram_manager._register_handlers(account.id, client)

                    results.append({
                        "phone": actual_phone,
                        "status": "imported",
                        "display_name": display_name
                    })

            except Exception as e:
                results.append({"phone": phone_hint or "unknown", "status": "error", "error": str(e)})

        db.session.commit()

    except zipfile.BadZipFile:
        return jsonify({"error": "Invalid ZIP file"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    imported = sum(1 for r in results if r["status"] == "imported")
    return jsonify({"results": results, "imported": imported, "total": len(results)})


async def _connect_and_get_info(client):
    """Connect a converted client and extract account info."""
    await client.connect()
    me = await client.get_me()
    phone = me.phone or ""
    display_name = f"{me.first_name or ''} {me.last_name or ''}".strip()

    # Try to get api_id from the client
    api_id = getattr(client, 'api_id', 0) or getattr(client.session, 'api_id', 0)
    api_hash = ""

    return {
        "phone": phone,
        "display_name": display_name,
        "api_id": api_id,
        "api_hash": api_hash
    }


def _find_tdata_dirs(root):
    """
    Walk the extracted directory to find tdata folders.
    Returns list of (phone_hint, tdata_path) tuples.
    """
    found = []
    for entry in os.listdir(root):
        entry_path = os.path.join(root, entry)
        if not os.path.isdir(entry_path):
            continue

        # Case 1: entry is a phone number folder containing tdata/
        tdata_sub = os.path.join(entry_path, 'tdata')
        if os.path.isdir(tdata_sub):
            phone_hint = entry.strip()
            found.append((phone_hint, tdata_sub))
            continue

        # Case 2: entry is 'tdata' directly
        if entry.lower() == 'tdata':
            found.append(("", entry_path))
            continue

        # Case 3: go one level deeper (macOS zip may add extra dir)
        for sub in os.listdir(entry_path):
            sub_path = os.path.join(entry_path, sub)
            if os.path.isdir(sub_path):
                tdata_deep = os.path.join(sub_path, 'tdata')
                if os.path.isdir(tdata_deep):
                    found.append((sub.strip(), tdata_deep))
                elif sub.lower() == 'tdata':
                    found.append((entry.strip(), sub_path))

    return found


@accounts_bp.route('/api/accounts/connect-all', methods=['POST'])
def connect_all_accounts():
    """Try to connect all disconnected accounts."""
    accounts = Account.query.filter_by(is_active=True).all()
    results = []

    for account in accounts:
        if telegram_manager.is_connected(account.id):
            results.append({"phone": account.phone, "status": "already_connected"})
            continue

        try:
            result = telegram_manager.run_async(
                telegram_manager.connect_account(
                    account.id, account.phone,
                    account.api_id, account.api_hash,
                    account.session_name
                )
            )
            if result["status"] == "connected":
                account.is_connected = True
                account.display_name = result.get("display_name", "")
            results.append({"phone": account.phone, "status": result["status"]})
        except Exception as e:
            results.append({"phone": account.phone, "status": "error", "error": str(e)})

    db.session.commit()
    return jsonify(results)


@accounts_bp.route('/api/accounts/<int:account_id>/connect', methods=['POST'])
def connect_account(account_id):
    account = db.session.get(Account, account_id)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    try:
        result = telegram_manager.run_async(
            telegram_manager.connect_account(
                account.id, account.phone,
                account.api_id, account.api_hash,
                account.session_name
            )
        )
        if result["status"] == "connected":
            account.is_connected = True
            account.display_name = result.get("display_name", "")
            db.session.commit()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route('/api/accounts/<int:account_id>/verify', methods=['POST'])
def verify_account(account_id):
    account = db.session.get(Account, account_id)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    data = request.json
    code = data.get('code', '').strip()
    password = data.get('password', '').strip() or None

    if not code:
        return jsonify({"error": "Verification code is required"}), 400

    try:
        result = telegram_manager.run_async(
            telegram_manager.verify_code(account.id, account.phone, code, password)
        )
        account.is_connected = True
        account.display_name = result.get("display_name", "")
        db.session.commit()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route('/api/accounts/<int:account_id>/disconnect', methods=['POST'])
def disconnect_account(account_id):
    account = db.session.get(Account, account_id)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    try:
        telegram_manager.run_async(telegram_manager.disconnect_account(account.id))
        account.is_connected = False
        db.session.commit()
        return jsonify({"status": "disconnected"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route('/api/accounts/<int:account_id>', methods=['DELETE'])
def delete_account(account_id):
    account = db.session.get(Account, account_id)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    if telegram_manager.is_connected(account.id):
        telegram_manager.run_async(telegram_manager.disconnect_account(account.id))

    db.session.delete(account)
    db.session.commit()
    return jsonify({"status": "deleted"})


@accounts_bp.route('/api/accounts/<int:account_id>/clear-flood', methods=['POST'])
def clear_flood(account_id):
    account = db.session.get(Account, account_id)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    account.is_flood_limited = False
    account.flood_until = None
    db.session.commit()
    return jsonify({"status": "flood cleared"})


@accounts_bp.route('/api/accounts/capacity')
def account_capacity():
    """Return remaining daily capacity per active account."""
    accounts = Account.query.filter_by(is_active=True).all()
    result = []
    for a in accounts:
        result.append({
            "id": a.id,
            "phone": a.phone,
            "daily_limit": a.daily_limit or 20,
            "effective_limit": _get_effective_limit(a),
            "remaining": get_remaining_capacity(a.id)
        })
    return jsonify(result)


@accounts_bp.route('/api/accounts/<int:account_id>/daily-limit', methods=['PUT'])
def set_daily_limit(account_id):
    account = db.session.get(Account, account_id)
    if not account:
        return jsonify({"error": "Account not found"}), 404
    data = request.json
    account.daily_limit = max(1, int(data.get('daily_limit', 20)))
    db.session.commit()
    return jsonify({"status": "updated", "daily_limit": account.daily_limit})


@accounts_bp.route('/api/accounts/<int:account_id>/clear-warmup', methods=['POST'])
def clear_warmup(account_id):
    account = db.session.get(Account, account_id)
    if not account:
        return jsonify({"error": "Account not found"}), 404
    account.warmup_start_date = None
    db.session.commit()
    return jsonify({"status": "warmup cleared"})
