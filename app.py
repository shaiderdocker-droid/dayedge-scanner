"""
DayEdge v3 - Flask Web Server
Uses background threads so long scans don't timeout
"""

from flask import Flask, jsonify, Response
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import run_scanner, run_morning_scan, run_backtest
import json, os, threading
import numpy as np
import yfinance as yf
from datetime import datetime

app = Flask(__name__)

latest_results = None
latest_morning = None
scan_status = {"running": False, "task": None, "started": None, "error": None}

def load_file(path):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except:
        pass
    return None

def run_scan_background():
    global latest_results, scan_status
    try:
        scan_status["running"] = True
        scan_status["error"] = None
        latest_results = run_scanner()
    except Exception as e:
        scan_status["error"] = str(e)
        print(f"Background scan error: {e}")
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

def scheduled_evening():
    global latest_results
    print(f"[SCHEDULER] Evening scan at {datetime.now()}")
    latest_results = run_scanner()

def scheduled_morning():
    global latest_morning
    print(f"[SCHEDULER] Morning scan at {datetime.now()}")
    latest_morning = run_morning_scan()

scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_evening, 'cron', day_of_week='mon-fri', hour=18, minute=0)
scheduler.add_job(scheduled_morning, 'cron', day_of_week='mon-fri', hour=9, minute=0)
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
    return jsonify(latest_results)

@app.route('/api/morning')
def get_morning():
    global latest_morning
    if latest_morning is None:
        latest_morning = load_file("morning_golist.json")
    if latest_morning is None:
        return jsonify({"golist": [], "message": "No morning scan yet."})
    return jsonify(latest_morning)

@app.route('/api/backtest')
def get_backtest():
    data = load_file("backtest_results.json")
    if data is None:
        return jsonify({"error": "No backtest run yet."})
    return jsonify(data)

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
        return jsonify({"status": "already_running", "message": "Scan already in progress"})
    scan_status["task"] = "evening"
    scan_status["started"] = datetime.now().isoformat()
    t = threading.Thread(target=run_scan_background, daemon=True)
    t.start()
    return jsonify({"status": "started", "message": "Scan started in background"})

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

@app.route('/api/quote/<symbol>')
def get_quote(symbol):
    """Live quote endpoint for the Exit Manager tab."""
    try:
        sym = symbol.upper().strip()
        ticker = yf.Ticker(sym)
        df = ticker.history(period="60d")
        if df is None or len(df) < 2:
            return jsonify({"error": f"No data found for {sym}"}), 404

        closes = df['Close'].tolist()
        highs  = df['High'].tolist()
        lows   = df['Low'].tolist()

        price      = round(float(closes[-1]), 2)
        prev       = round(float(closes[-2]), 2)
        change     = round(price - prev, 2)
        change_pct = round(((price - prev) / prev) * 100, 2)

        # 7-day ATR
        atr_vals = [(highs[i] - lows[i]) for i in range(-7, 0)]
        atr      = round(float(sum(atr_vals) / len(atr_vals)), 2)
        atr_pct  = round((atr / price) * 100, 2)

        high_52w = round(float(max(highs)), 2)
        low_52w  = round(float(min(lows)), 2)

        try:
            info    = ticker.info
            avg_vol = int(info.get("averageVolume", 0) or 0)
            name    = info.get("shortName", sym)
        except:
            avg_vol = 0
            name    = sym

        return jsonify({
            "symbol":     sym,
            "name":       name,
            "price":      price,
            "prev_close": prev,
            "change":     change,
            "change_pct": change_pct,
            "atr":        atr,
            "atr_pct":    atr_pct,
            "high_52w":   high_52w,
            "low_52w":    low_52w,
            "avg_volume": avg_vol,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
