from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import run_scanner
import json
import os
from datetime import datetime

app = Flask(__name__)

latest_results = None

def scheduled_scan():
    global latest_results
    print(f"[SCHEDULER] Running evening scan at {datetime.now()}")
    latest_results = run_scanner()

scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_scan, 'cron', day_of_week='mon-fri', hour=18, minute=0)
scheduler.start()

@app.route('/')
def index():
    with open(os.path.join(os.path.dirname(__file__), 'static', 'index.html'), 'r') as f:
        content = f.read()
    from flask import Response
    return Response(content, mimetype='text/html')

@app.route('/api/scan')
def get_scan_results():
    global latest_results
    if latest_results is None:
        if os.path.exists('scan_results.json'):
            with open('scan_results.json') as f:
                latest_results = json.load(f)
        else:
            return jsonify({"error": "No scan results yet. Click Run Scan Now to start.", "results": []})
    return jsonify(latest_results)

@app.route('/api/run-scan', methods=['POST'])
def trigger_scan():
    global latest_results
    try:
        latest_results = run_scanner()
        return jsonify({"status": "success", "message": "Scan complete!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

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
