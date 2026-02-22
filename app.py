"""
DayEdge v3 - Flask Web Server
Schedules: Evening scan at 6pm ET, Morning scan at 9am ET
"""
from flask import Flask, jsonify, Response
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import run_scanner, run_morning_scan, run_backtest
import json, os, threading
import numpy as np
from datetime import datetime

app = Flask(__name__)

latest_results = None
latest_morning = None
scan_status = {"running": False, "task": None, "error": None}

def make_serializable(obj):
    if isinstance(obj, dict):
        return {k: make_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_serializable(i) for i in obj]
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    return obj

def load_file(path):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except: pass
    return None

def run_scan_background():
    global latest_results, scan_status
    try:
        scan_status["running"] = True
        scan_status["error"] = None
        latest_results = run_scanner()
    except Exception as e:
        scan_status["error"] = str(e)
    finally:
        scan_status["running"] = False

def run_morning_background():
    global latest_morning, scan_status
    try:
        scan_status["running"] = True
        scan_status["error"] = None
        latest_morning = run_morning_scan()
    except Exception as e:
        scan_status["error"] = str(e)
    finally:
        scan_status["running"] = False

def run_backtest_background():
    global scan_status
    try:
        scan_status["running"] = True
        scan_status["error"] = None
        run_backtest()
    except Exception as e:
        scan_status["error"] = str(e)
    finally:
        scan_status["running"] = False

def scheduled_evening_scan():
    global latest_results
    print(f"[SCHEDULER] Evening scan at {datetime.now()}")
    latest_results = run_scanner()

def scheduled_morning_scan():
    global latest_morning
    print(f"[SCHEDULER] Morning scan at {datetime.now()}")
    latest_morning = run_morning_scan()

scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_evening_scan, 'cron', day_of_week='mon-fri', hour=18, minute=0)
scheduler.add_job(scheduled_morning_scan, 'cron', day_of_week='mon-fri', hour=9, minute=0)
scheduler.start()

@app.route('/')
def index():
    with open(os.path.join(os.path.dirname(__file__), 'static', 'index.html'), 'r') as f:
        return Response(f.read(), mimetype='text/html')

@app.route('/api/scan')
def get_scan():
    global latest_results
    if latest_results is None:
        latest_results = load_file("scan_results.json")
    if latest_results is None:
        return jsonify({"error": "No scan results yet. Click Run Scan Now.", "results": []})
    return jsonify(make_serializable(latest_results))

@app.route('/api/morning')
def get_morning():
    global latest_morning
    if latest_morning is None:
        latest_morning = load_file("morning_golist.json")
    if latest_morning is None:
        return jsonify({"golist": [], "message": "No morning scan yet. Runs at 9am ET."})
    return jsonify(make_serializable(latest_morning))

@app.route('/api/backtest')
def get_backtest():
    data = load_file("backtest_results.json")
    if data is None:
        return jsonify({"error": "No backtest run yet. Click Run Backtest."})
    return jsonify(make_serializable(data))

@app.route('/api/scan-status')
def get_scan_status():
    return jsonify({
        "running": scan_status["running"],
        "task": scan_status["task"],
        "error": scan_status["error"],
        "has_results": latest_results is not None
    })

@app.route('/api/run-scan', methods=['POST'])
def trigger_scan():
    global scan_status
    if scan_status["running"]:
        return jsonify({"status": "already_running"})
    scan_status["task"] = "evening"
    t = threading.Thread(target=run_scan_background, daemon=True)
    t.start()
    return jsonify({"status": "started"})

@app.route('/api/run-morning', methods=['POST'])
def trigger_morning():
    global scan_status
    if scan_status["running"]:
        return jsonify({"status": "already_running"})
    scan_status["task"] = "morning"
    t = threading.Thread(target=run_morning_background, daemon=True)
    t.start()
    return jsonify({"status": "started"})

@app.route('/api/run-backtest', methods=['POST'])
def trigger_backtest():
    global scan_status
    if scan_status["running"]:
        return jsonify({"status": "already_running"})
    scan_status["task"] = "backtest"
    t = threading.Thread(target=run_backtest_background, daemon=True)
    t.start()
    return jsonify({"status": "started"})

@app.route('/api/status')
def status():
    return jsonify({
        "status": "running",
        "time": datetime.now().isoformat(),
        "has_results": latest_results is not None
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
