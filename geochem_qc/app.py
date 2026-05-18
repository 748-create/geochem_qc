"""
app.py — GeoQC Pro Flask server.
Handles: file upload, pre-analysis, full pipeline, file download.
WebSocket for live progress.
"""

import os
import json
import threading
import traceback
from pathlib import Path
from datetime import datetime

from flask import (Flask, request, jsonify, send_file,
                   send_from_directory, abort)

app = Flask(__name__, static_folder='static', static_url_path='')

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# In-memory progress store (keyed by job_id)
JOBS: dict = {}


# ─── Static UI ───────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


# ─── Upload ──────────────────────────────────────────────────────────────────

@app.route('/api/upload', methods=['POST'])
def upload_files():
    """Accept file uploads, return file IDs."""
    uploaded = []
    for key, file in request.files.items():
        if file.filename:
            safe_name = f"{datetime.now().strftime('%H%M%S%f')}_{file.filename}"
            path = UPLOAD_DIR / safe_name
            file.save(str(path))
            uploaded.append({'id': safe_name, 'name': file.filename, 'path': str(path)})
    return jsonify({'files': uploaded})


# ─── Pre-analysis ─────────────────────────────────────────────────────────────

@app.route('/api/pre-analysis', methods=['POST'])
def pre_analysis():
    """Quick pre-analysis: parse + merge + summarize. Returns Screen 3 data."""
    data = request.json
    analytical_ids = data.get('analytical_files', [])
    dispatch_id = data.get('dispatch_file')

    analytical_paths = [str(UPLOAD_DIR / fid) for fid in analytical_ids
                        if (UPLOAD_DIR / fid).exists()]
    dispatch_path = str(UPLOAD_DIR / dispatch_id) if dispatch_id and (UPLOAD_DIR / dispatch_id).exists() else None

    if not analytical_paths:
        return jsonify({'error': 'No valid analytical files provided'}), 400

    try:
        from qc_engine.orchestrator import run_pre_analysis_only
        result = run_pre_analysis_only(analytical_paths, dispatch_path)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


# ─── Full pipeline (async with progress) ─────────────────────────────────────

@app.route('/api/run', methods=['POST'])
def run_pipeline():
    """Start async pipeline. Returns job_id."""
    data = request.json
    mode = data.get('mode', 'A').upper()
    analytical_ids = data.get('analytical_files', [])
    dispatch_id = data.get('dispatch_file')
    conflict_resolutions = data.get('conflict_resolutions', {})
    zone = data.get('zone')

    analytical_paths = [str(UPLOAD_DIR / fid) for fid in analytical_ids
                        if (UPLOAD_DIR / fid).exists()]
    dispatch_path = str(UPLOAD_DIR / dispatch_id) if dispatch_id and (UPLOAD_DIR / dispatch_id).exists() else None

    if not analytical_paths:
        return jsonify({'error': 'No valid analytical files provided'}), 400

    job_id = datetime.now().strftime('%Y%m%d%H%M%S%f')
    JOBS[job_id] = {'status': 'running', 'progress': 0, 'message': 'Starting...', 'result': None}

    def _run():
        from qc_engine.orchestrator import run_pipeline as _pipeline

        def _cb(pct, msg):
            JOBS[job_id]['progress'] = pct
            JOBS[job_id]['message'] = msg

        result = _pipeline(
            mode=mode,
            analytical_files=analytical_paths,
            dispatch_file=dispatch_path,
            conflict_resolutions=conflict_resolutions,
            progress_callback=_cb,
            zone=zone,
        )
        JOBS[job_id]['status'] = 'done' if result.get('success') else 'error'
        JOBS[job_id]['result'] = result
        JOBS[job_id]['progress'] = 100

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({'job_id': job_id})


@app.route('/api/progress/<job_id>')
def get_progress(job_id: str):
    """Poll for job progress."""
    job = JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify({
        'status': job['status'],
        'progress': job['progress'],
        'message': job['message'],
        'result': job['result'] if job['status'] in ('done', 'error') else None
    })


# ─── Download outputs ─────────────────────────────────────────────────────────

@app.route('/api/download/<path:filename>')
def download_file(filename: str):
    """Download an output file."""
    filepath = OUTPUT_DIR / filename
    if not filepath.exists():
        abort(404)
    return send_file(str(filepath), as_attachment=True)


@app.route('/api/outputs')
def list_outputs():
    """List recent output files."""
    files = []
    for f in sorted(OUTPUT_DIR.iterdir(), reverse=True)[:20]:
        if f.is_file():
            files.append({'name': f.name, 'size': f.stat().st_size, 'ext': f.suffix})
    return jsonify({'files': files})


# ─── Config ──────────────────────────────────────────────────────────────────

@app.route('/api/config', methods=['GET'])
def get_config():
    cfg_path = BASE_DIR / 'config.json'
    with open(cfg_path) as f:
        return jsonify(json.load(f))


@app.route('/api/config', methods=['POST'])
def save_config():
    cfg_path = BASE_DIR / 'config.json'
    data = request.json
    with open(cfg_path, 'w') as f:
        json.dump(data, f, indent=2)
    return jsonify({'ok': True})


# ─── History ─────────────────────────────────────────────────────────────────

@app.route('/api/history')
def get_history():
    zone = request.args.get('zone')
    from qc_engine.memory.historique import list_batches
    return jsonify({'batches': list_batches(zone=zone)})


if __name__ == '__main__':
    print("═" * 50)
    print("  GeoQC Pro — Module 2")
    print("  http://localhost:5000")
    print("═" * 50)
    app.run(debug=False, port=5000, threaded=True)
