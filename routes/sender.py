import json
import uuid
import queue
import threading
from flask import Blueprint, request, jsonify, Response, stream_with_context
from services.telegram_service import telegram_manager
from services.send_service import bulk_send, cancel_send

sender_bp = Blueprint('sender', __name__)

# Persistent job state — survives page refreshes
_jobs = {}  # job_id -> { "status": "running"|"done", "log": [...], "total": N }
_jobs_lock = threading.Lock()
_progress_queues = {}


def _drain_worker(job_id):
    """Background thread that drains the progress queue into persistent job state."""
    q = _progress_queues.get(job_id)
    if not q:
        return

    while True:
        try:
            event = q.get(timeout=120)
            if event is None:
                with _jobs_lock:
                    if job_id in _jobs:
                        _jobs[job_id]["status"] = "done"
                return

            with _jobs_lock:
                if job_id in _jobs:
                    _jobs[job_id]["log"].append(event)
        except queue.Empty:
            continue


@sender_bp.route('/send')
def send_page():
    from flask import render_template
    return render_template('send.html')


@sender_bp.route('/api/send/start', methods=['POST'])
def start_send():
    data = request.json
    contact_ids = data.get('contact_ids', [])
    message_id = data.get('message_id')
    account_ids = data.get('account_ids', [])
    delay_min = data.get('delay_min', 30)
    delay_max = data.get('delay_max', 60)
    per_account_limit = data.get('per_account_limit', 0)
    scan_gap_minutes = data.get('scan_gap_minutes', 0)

    if not contact_ids or not message_id or not account_ids:
        return jsonify({"error": "contact_ids, message_id, and account_ids are required"}), 400

    job_id = str(uuid.uuid4())
    progress_queue = queue.Queue()
    _progress_queues[job_id] = progress_queue

    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "log": [], "total": len(contact_ids)}

    # Start drain thread
    t = threading.Thread(target=_drain_worker, args=(job_id,), daemon=True)
    t.start()

    telegram_manager.run_async_nonblocking(
        bulk_send(job_id, contact_ids, message_id, account_ids,
                  delay_min, delay_max, progress_queue,
                  per_account_limit=per_account_limit,
                  scan_gap_minutes=scan_gap_minutes)
    )

    return jsonify({"job_id": job_id})


@sender_bp.route('/api/send/active')
def active_send():
    """Return the currently running send job (if any) so the UI can reconnect."""
    with _jobs_lock:
        for job_id, job in _jobs.items():
            if job["status"] == "running":
                return jsonify({"job_id": job_id, "total": job.get("total", 0)})
    return jsonify({"job_id": None})


@sender_bp.route('/api/send/progress/<job_id>')
def send_progress(job_id):
    """SSE stream. Replays past events first, then streams new ones live."""
    def generate():
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                return

        # Replay all past events
        sent = 0
        with _jobs_lock:
            for event in job["log"]:
                yield f"data: {json.dumps(event)}\n\n"
                sent += 1

        # If already done after replay
        with _jobs_lock:
            if job["status"] == "done":
                yield f"data: {json.dumps({'done': True})}\n\n"
                return

        # Stream new events as they come
        while True:
            with _jobs_lock:
                log = job["log"]
                new_events = log[sent:]
                is_done = job["status"] == "done"

            for event in new_events:
                yield f"data: {json.dumps(event)}\n\n"
                sent += 1

            if is_done:
                yield f"data: {json.dumps({'done': True})}\n\n"
                return

            try:
                import time
                time.sleep(0.3)
            except GeneratorExit:
                return

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@sender_bp.route('/api/send/stop/<job_id>', methods=['POST'])
def stop_send(job_id):
    cancel_send(job_id)
    return jsonify({"status": "cancel requested"})


@sender_bp.route('/api/send/clear/<job_id>', methods=['POST'])
def clear_send(job_id):
    """Clear a finished job so a new one can start."""
    with _jobs_lock:
        _jobs.pop(job_id, None)
    _progress_queues.pop(job_id, None)
    return jsonify({"status": "cleared"})
