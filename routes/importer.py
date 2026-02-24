import json
import uuid
import queue
import threading
from flask import Blueprint, render_template, request, jsonify, Response, stream_with_context
from services.telegram_service import telegram_manager
from services.import_service import bulk_import, cancel_import

importer_bp = Blueprint('importer', __name__)

# Persistent job state — survives page refreshes
_jobs = {}  # job_id -> { "status": "running"|"done", "log": [...], "summary": {...} }
_jobs_lock = threading.Lock()
_progress_queues = {}  # job_id -> Queue (for the async task to push events)


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


@importer_bp.route('/api/import/start', methods=['POST'])
def start_import():
    data = request.json
    contact_ids = data.get('contact_ids', [])
    account_ids = data.get('account_ids', [])
    delay_min = data.get('delay_min', 5)
    delay_max = data.get('delay_max', 10)

    # Backwards compat: accept single account_id too
    if not account_ids and data.get('account_id'):
        account_ids = [data['account_id']]

    if not contact_ids or not account_ids:
        return jsonify({"error": "contact_ids and account_ids are required"}), 400

    job_id = str(uuid.uuid4())
    progress_queue = queue.Queue()
    _progress_queues[job_id] = progress_queue

    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "log": [], "total": len(contact_ids)}

    # Start drain thread that persists events from the queue
    t = threading.Thread(target=_drain_worker, args=(job_id,), daemon=True)
    t.start()

    telegram_manager.run_async_nonblocking(
        bulk_import(job_id, contact_ids, account_ids, delay_min, delay_max, progress_queue)
    )

    return jsonify({"job_id": job_id})


@importer_bp.route('/api/import/active')
def active_import():
    """Return the currently running import job (if any) so the UI can reconnect."""
    with _jobs_lock:
        for job_id, job in _jobs.items():
            if job["status"] == "running":
                return jsonify({"job_id": job_id, "total": job.get("total", 0)})
    return jsonify({"job_id": None})


@importer_bp.route('/api/import/progress/<job_id>')
def import_progress(job_id):
    """SSE stream. Sends all past log entries first (replay), then streams new ones live."""
    def generate():
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                return

        # Replay: send all events that already happened
        sent = 0
        with _jobs_lock:
            for event in job["log"]:
                yield f"data: {json.dumps(event)}\n\n"
                sent += 1

        # If already done after replay, send done
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

            # Poll interval
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


@importer_bp.route('/api/import/stop/<job_id>', methods=['POST'])
def stop_import(job_id):
    cancel_import(job_id)
    return jsonify({"status": "cancel requested"})


@importer_bp.route('/api/import/clear/<job_id>', methods=['POST'])
def clear_import(job_id):
    """Clear a finished job so a new one can start."""
    with _jobs_lock:
        _jobs.pop(job_id, None)
    _progress_queues.pop(job_id, None)
    return jsonify({"status": "cleared"})
