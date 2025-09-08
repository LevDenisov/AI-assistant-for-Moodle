from __future__ import annotations
import threading
from flask import Flask, request, jsonify, send_from_directory
from repository import set_job_result
from config import WEBHOOK_PORT, UPLOAD_DIR

_app = Flask("llm-callback-server")
_started = False

@_app.post("/callback")
def callback():
    try:
        data = request.get_json(force=True, silent=True) or {}
        submission_id = data.get("submission_id")
        task_id = data.get("task_id")
        result = data.get("result")
        if not submission_id or not task_id or not isinstance(result, dict):
            return jsonify({"ok": False, "error": "bad payload"}), 400
        set_job_result(submission_id, task_id, result)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@_app.get("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False, max_age=0)

def start_once() -> None:
    global _started
    if _started:
        return
    def _run():
        _app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False, use_reloader=False)
    threading.Thread(target=_run, daemon=True).start()
    _started = True
