"""
DayEdge v3 - Flask Web Server
Schedules: Evening scan at 6pm ET, Morning scan at 9am ET
"""

from flask import Flask, jsonify, Response
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import run_scanner, run_morning_scan, run_backtest
import json, os
from datetime import datetime

app = Flask(__name__)

latest_results = None
latest_morning = None

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

def load_file(path):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except: pass
    return None

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
    return jsonify(latest_results)

@app.route('/api/morning')
def get_morning():
    global latest_morning
    if latest_morning is None:
        latest_morning = load_file("morning_golist.json")
    if latest_morning is None:
        return jsonify({"golist": [], "message": "No morning scan yet. Runs at 9am ET."})
    return jsonify(latest_morning)

@app.route('/api/backtest')
def get_backtest():
    data = load_file("backtest_results.json")
    if data is None:
        return jsonify({"error": "No backtest run yet. Click Run Backtest."})
    return jsonify(data)

@app.route('/api/run-scan', methods=['POST'])
def trigger_scan():
    global latest_results
    try:
        latest_results = run_scanner()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/run-morning', methods=['POST'])
def trigger_morning():
    global latest_morning
    try:
        latest_morning = run_morning_scan()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/run-backtest', methods=['POST'])
def trigger_backtest():
    try:
        result = run_backtest()
        return jsonify({"status": "success", "data": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/status')
def status():
    return jsonify({
        "status": "running",
        "time": datetime.now().isoformat(),
        "has_results": latest_results is not None,
        "has_morning": latest_morning is not None
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
