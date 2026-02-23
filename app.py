"""
DayEdge v5 - Flask Web Server
All 8 live trading enhancements:
1. Live intraday tracker
2. Entry timing / pullback signal
3. Pre-market momentum ranker
4. Pattern recognition from EOD history
5. Risk dashboard
6. Gap quality scorer
7. SPY live condition
8. Weekly performance journal
"""

from flask import Flask, jsonify, Response
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import run_scanner, run_morning_scan, run_backtest
import json, os, threading, statistics
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

app = Flask(__name__)

latest_results = None
latest_morning = None
scan_status = {"running": False, "task": None, "started": None, "error": None}

# ── HELPERS ──────────────────────────────────────────────────────────────────

def load_file(path):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except:
        pass
    return None

def save_file(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Save error {path}: {e}")

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

# ── BACKGROUND TASKS ─────────────────────────────────────────────────────────

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

# ── SCHEDULER ────────────────────────────────────────────────────────────────

def scheduled_evening():
    global latest_results
    print(f"[SCHEDULER] Evening scan at {datetime.now()}")
    latest_results = run_scanner()

def scheduled_morning():
    global latest_morning
    print(f"[SCHEDULER] Morning scan at {datetime.now()}")
    latest_morning = run_morning_scan()

def scheduled_eod_save():
    """Auto-save EOD results at 4:15pm ET and append to history."""
    print(f"[SCHEDULER] Auto EOD save at {datetime.now()}")
    try:
        eod = load_file("eod_results.json")
        if eod and eod.get("date") == datetime.now().strftime("%Y-%m-%d"):
            history = load_file("eod_history.json") or []
            # Avoid duplicate dates
            history = [h for h in history if h.get("date") != eod["date"]]
            history.append(eod)
            history = history[-60:]  # keep 60 days
            save_file("eod_history.json", history)
            print("[SCHEDULER] EOD history saved")
    except Exception as e:
        print(f"[SCHEDULER] EOD save error: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_evening,  'cron', day_of_week='mon-fri', hour=18, minute=0)
scheduler.add_job(scheduled_morning,  'cron', day_of_week='mon-fri', hour=9,  minute=0)
scheduler.add_job(scheduled_eod_save, 'cron', day_of_week='mon-fri', hour=16, minute=15)
scheduler.start()

# ── CORE ROUTES ───────────────────────────────────────────────────────────────

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
        return jsonify({"golist": [], "message": "No morning scan yet."})
    return jsonify(make_serializable(latest_morning))

@app.route('/api/backtest')
def get_backtest():
    data = load_file("backtest_results.json")
    if data is None:
        return jsonify({"error": "No backtest run yet."})
    return jsonify(make_serializable(data))

@app.route('/api/scan-status')
def get_scan_status():
    return jsonify({
        "running": scan_status["running"],
        "task":    scan_status["task"],
        "error":   scan_status["error"],
        "has_results": latest_results is not None
    })

@app.route('/api/run-scan', methods=['POST'])
def trigger_scan():
    global scan_status
    if scan_status["running"]:
        return jsonify({"status": "already_running"})
    scan_status["task"] = "evening"
    scan_status["started"] = datetime.now().isoformat()
    threading.Thread(target=run_scan_background, daemon=True).start()
    return jsonify({"status": "started"})

@app.route('/api/run-morning', methods=['POST'])
def trigger_morning():
    global scan_status
    if scan_status["running"]:
        return jsonify({"status": "already_running"})
    scan_status["task"] = "morning"
    threading.Thread(target=run_morning_background, daemon=True).start()
    return jsonify({"status": "started"})

@app.route('/api/run-backtest', methods=['POST'])
def trigger_backtest():
    global scan_status
    if scan_status["running"]:
        return jsonify({"status": "already_running"})
    scan_status["task"] = "backtest"
    threading.Thread(target=run_backtest_background, daemon=True).start()
    return jsonify({"status": "started"})

# ── FEATURE 1: LIVE INTRADAY TRACKER ─────────────────────────────────────────

@app.route('/api/live-tracker')
def live_tracker():
    """Real-time prices for morning go-list. Shows P&L vs entry and target zones."""
    morning = load_file("morning_golist.json")
    if not morning or not morning.get("golist"):
        return jsonify({"error": "No morning go-list. Run morning scan first.", "stocks": []})

    stocks = []
    for s in morning["golist"]:
        sym = s["symbol"]
        entry = s.get("trade_levels", {}).get("entry") or s.get("prev_close")
        if not entry:
            continue
        try:
            ticker = yf.Ticker(sym)
            df = ticker.history(period="1d", interval="5m")
            if df is None or len(df) == 0:
                continue

            price     = round(float(df['Close'].iloc[-1]), 2)
            day_high  = round(float(df['High'].max()), 2)
            day_low   = round(float(df['Low'].min()), 2)
            day_vol   = int(df['Volume'].sum())
            pnl_pct   = round(((price - entry) / entry) * 100, 2)
            pnl_dollar = round(price - entry, 2)

            tl = s.get("trade_levels", {})
            t1 = tl.get("target1")
            t2 = tl.get("target2")
            t3 = tl.get("target3")
            stop = tl.get("stop")

            # Which zone is price in?
            if stop and price <= stop:
                zone = "STOPPED"
            elif t3 and price >= t3:
                zone = "T3"
            elif t2 and price >= t2:
                zone = "T2"
            elif t1 and price >= t1:
                zone = "T1"
            elif price > entry:
                zone = "ABOVE_ENTRY"
            else:
                zone = "BELOW_ENTRY"

            # Pullback signal: price within 0.5% of VWAP (approx from typical price)
            typical = df[['High','Low','Close']].mean(axis=1)
            cum_tp_vol = (typical * df['Volume']).cumsum()
            cum_vol = df['Volume'].cumsum()
            vwap = round(float((cum_tp_vol / cum_vol).iloc[-1]), 2)

            pullback_signal = abs(price - vwap) / vwap < 0.005

            # Check if high was above T1 at any point (potential missed exit)
            t1_was_touched = bool(t1 and day_high >= t1)

            stocks.append({
                "symbol":       sym,
                "grade":        s.get("grade", "C"),
                "entry":        round(float(entry), 2),
                "price":        price,
                "vwap":         vwap,
                "day_high":     day_high,
                "day_low":      day_low,
                "day_vol":      day_vol,
                "pnl_pct":      pnl_pct,
                "pnl_dollar":   pnl_dollar,
                "zone":         zone,
                "pullback_signal": pullback_signal,
                "t1_was_touched": t1_was_touched,
                "target1":      t1,
                "target2":      t2,
                "target3":      t3,
                "stop":         stop,
                "evening_score": s.get("evening_score", 0),
            })
        except Exception as e:
            print(f"Live tracker error {sym}: {e}")
            continue

    stocks.sort(key=lambda x: x["pnl_pct"], reverse=True)
    return jsonify({"stocks": stocks, "timestamp": datetime.now().isoformat()})

# ── FEATURE 2 & 3: ENTRY TIMING + PRE-MARKET MOMENTUM ────────────────────────

@app.route('/api/premarket-momentum')
def premarket_momentum():
    """Pre-market momentum ranker — volume surge and price acceleration."""
    morning = load_file("morning_golist.json")
    golist  = (morning or {}).get("golist", [])

    # Also check evening scan for any high-scorers not in morning list
    evening = load_file("scan_results.json")
    evening_results = (evening or {}).get("results", [])
    evening_map = {r["symbol"]: r for r in evening_results}

    symbols = list({s["symbol"] for s in golist})
    if not symbols:
        return jsonify({"error": "No go-list stocks. Run morning scan first.", "stocks": []})

    stocks = []
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            # Pre-market data (1m bars today)
            df_pm = ticker.history(period="1d", interval="1m", prepost=True)
            df_60 = ticker.history(period="60d", interval="1d")

            if df_pm is None or len(df_pm) == 0:
                continue

            prev_close = round(float(df_60['Close'].iloc[-2]), 2) if len(df_60) >= 2 else None

            # Pre-market only rows (before 9:30am)
            df_pm.index = df_pm.index.tz_convert('America/New_York')
            pm = df_pm[df_pm.index.hour < 9]
            last15 = pm.tail(15)

            pm_price  = round(float(df_pm['Close'].iloc[-1]), 2) if len(df_pm) else None
            pm_vol    = int(pm['Volume'].sum()) if len(pm) else 0
            last15_vol = int(last15['Volume'].sum()) if len(last15) else 0
            avg_daily_vol = int(df_60['Volume'].mean()) if len(df_60) else 1

            pm_pct = round(((pm_price - prev_close) / prev_close) * 100, 2) if prev_close and pm_price else 0

            # Acceleration: last 15min vol vs rest of pre-market
            early_vol = pm_vol - last15_vol
            acceleration = round(last15_vol / max(early_vol, 1), 2)

            # PM volume as % of avg daily
            pm_vol_ratio = round((pm_vol / max(avg_daily_vol, 1)) * 100, 1)

            # Entry timing signal
            # Good entry: price pulled back from PM high, near VWAP
            pm_high = round(float(pm['High'].max()), 2) if len(pm) else pm_price
            pullback_from_high = round(((pm_high - pm_price) / pm_high) * 100, 2) if pm_high else 0
            entry_signal = "WAIT" if pm_pct > 5 else "WATCH" if pm_pct > 2 else "WEAK"
            if pullback_from_high > 1 and pm_pct > 2:
                entry_signal = "PULLBACK — GOOD ENTRY ZONE"

            # Gap quality
            gap_quality = score_gap_quality(sym, pm_pct, ticker)

            morning_stock = next((s for s in golist if s["symbol"] == sym), {})
            evening_stock = evening_map.get(sym, {})

            stocks.append({
                "symbol":          sym,
                "grade":           morning_stock.get("grade", evening_stock.get("grade", "C")),
                "prev_close":      prev_close,
                "pm_price":        pm_price,
                "pm_pct":          pm_pct,
                "pm_vol":          pm_vol,
                "pm_vol_ratio":    pm_vol_ratio,
                "last15_vol":      last15_vol,
                "acceleration":    acceleration,
                "pm_high":         pm_high,
                "pullback_from_high": pullback_from_high,
                "entry_signal":    entry_signal,
                "gap_quality":     gap_quality,
                "evening_score":   morning_stock.get("evening_score", evening_stock.get("score", 0)),
            })
        except Exception as e:
            print(f"PM momentum error {sym}: {e}")
            continue

    # Rank: high PM%, high acceleration, good gap quality
    stocks.sort(key=lambda x: (x["pm_vol_ratio"] + x["acceleration"] * 20 + x["pm_pct"] * 2), reverse=True)
    return jsonify({"stocks": stocks, "timestamp": datetime.now().isoformat()})

def score_gap_quality(sym, gap_pct, ticker_obj):
    """Feature 6: Score gap by catalyst quality."""
    try:
        news = ticker_obj.news or []
        headlines = " ".join([n.get("title","").lower() for n in news[:5]])

        earnings_kw = ["earnings","beat","eps","revenue","quarterly","q1","q2","q3","q4"]
        upgrade_kw  = ["upgrade","overweight","buy rating","price target raised","outperform"]
        fda_kw      = ["fda","approval","clearance","trial","phase"]
        deal_kw     = ["merger","acquisition","deal","partnership","contract","awarded"]
        general_kw  = ["rises","gains","jumps","surges","rallies"]

        if any(k in headlines for k in earnings_kw):
            return {"label": "EARNINGS", "color": "green",  "score": 4, "note": "Earnings catalyst — high conviction"}
        elif any(k in headlines for k in upgrade_kw):
            return {"label": "UPGRADE",  "color": "green",  "score": 3, "note": "Analyst upgrade — strong catalyst"}
        elif any(k in headlines for k in fda_kw):
            return {"label": "FDA/DRUG", "color": "green",  "score": 4, "note": "FDA catalyst — high volatility expected"}
        elif any(k in headlines for k in deal_kw):
            return {"label": "DEAL",     "color": "blue",   "score": 3, "note": "M&A or deal — sustained move likely"}
        elif any(k in headlines for k in general_kw):
            return {"label": "GENERAL",  "color": "yellow", "score": 2, "note": "General news — moderate conviction"}
        elif abs(gap_pct) > 3:
            return {"label": "NO NEWS",  "color": "red",    "score": 1, "note": "Large gap with no clear catalyst — caution"}
        else:
            return {"label": "UNKNOWN",  "color": "dim",    "score": 1, "note": "No clear catalyst found"}
    except:
        return {"label": "UNKNOWN", "color": "dim", "score": 1, "note": "Could not fetch news"}

# ── FEATURE 5: RISK DASHBOARD ─────────────────────────────────────────────────

@app.route('/api/risk-dashboard')
def risk_dashboard():
    """Pre-market risk summary: total exposure, max loss, position sizing guide."""
    morning = load_file("morning_golist.json")
    golist  = (morning or {}).get("golist", [])

    if not golist:
        return jsonify({"error": "No go-list. Run morning scan first.", "stocks": []})

    # Default account size — user can override via query param
    from flask import request
    account = float(request.args.get("account", 25000))
    risk_per_trade_pct = float(request.args.get("risk_pct", 1.0))  # 1% per trade default

    stocks = []
    total_risk_dollar = 0

    for s in golist:
        sym = s["symbol"]
        tl  = s.get("trade_levels", {})
        entry = tl.get("entry") or s.get("prev_close")
        stop  = tl.get("stop")

        if not entry or not stop:
            continue

        risk_per_share = round(float(entry) - float(stop), 2)
        stop_pct       = round((risk_per_share / float(entry)) * 100, 2)

        # Max shares to risk exactly risk_per_trade_pct of account
        max_risk_dollar = account * (risk_per_trade_pct / 100)
        shares_suggested = int(max_risk_dollar / risk_per_share) if risk_per_share > 0 else 0
        position_value   = round(shares_suggested * float(entry), 2)
        position_pct_of_account = round((position_value / account) * 100, 1)

        total_risk_dollar += max_risk_dollar

        stocks.append({
            "symbol":          sym,
            "grade":           s.get("grade", "C"),
            "entry":           round(float(entry), 2),
            "stop":            round(float(stop), 2),
            "risk_per_share":  risk_per_share,
            "stop_pct":        stop_pct,
            "shares_suggested": shares_suggested,
            "position_value":  position_value,
            "position_pct":    position_pct_of_account,
            "max_risk_dollar": round(max_risk_dollar, 2),
            "target1":         tl.get("target1"),
            "target2":         tl.get("target2"),
            "rr_ratio":        s.get("rr_ratio", 0),
            "evening_score":   s.get("evening_score", 0),
        })

    total_exposure  = round(sum(s["position_value"] for s in stocks), 2)
    total_risk      = round(total_risk_dollar, 2)
    total_risk_pct  = round((total_risk / account) * 100, 1)

    return jsonify({
        "account":        account,
        "risk_per_trade": risk_per_trade_pct,
        "stocks":         stocks,
        "summary": {
            "total_stocks":    len(stocks),
            "total_exposure":  total_exposure,
            "total_risk":      total_risk,
            "total_risk_pct":  total_risk_pct,
            "exposure_pct":    round((total_exposure / account) * 100, 1),
            "warning":         total_risk_pct > 5,
        },
        "timestamp": datetime.now().isoformat()
    })

# ── FEATURE 7: SPY LIVE CONDITION ─────────────────────────────────────────────

@app.route('/api/spy-condition')
def spy_condition():
    """Live SPY trend — green/yellow/red signal for intraday."""
    try:
        spy = yf.Ticker("SPY")
        df  = spy.history(period="1d", interval="5m")
        if df is None or len(df) < 5:
            return jsonify({"status": "unknown", "message": "No SPY data"})

        price  = round(float(df['Close'].iloc[-1]), 2)
        open_  = round(float(df['Open'].iloc[0]),  2)
        high   = round(float(df['High'].max()), 2)
        low    = round(float(df['Low'].min()),  2)
        change = round(price - open_, 2)
        chg_pct = round((change / open_) * 100, 2)

        # 9 EMA on 5m bars
        ema9 = df['Close'].ewm(span=9).mean().iloc[-1]
        above_ema9 = price > ema9

        # VWAP
        typical = df[['High','Low','Close']].mean(axis=1)
        vwap = float((typical * df['Volume']).cumsum().iloc[-1] / df['Volume'].cumsum().iloc[-1])
        above_vwap = price > vwap

        # Recent 3 candles direction
        last3 = df['Close'].tail(3).tolist()
        trending_up = last3[-1] > last3[0]

        if chg_pct > 0.3 and above_vwap and above_ema9:
            status = "bullish"
            message = "SPY trending up — good conditions for longs"
        elif chg_pct < -0.3 and not above_vwap:
            status = "bearish"
            message = "SPY selling off — tighten stops, avoid new entries"
        else:
            status = "neutral"
            message = "SPY choppy — be selective, wait for clear setups"

        return jsonify({
            "status":      status,
            "price":       price,
            "open":        open_,
            "high":        high,
            "low":         low,
            "change":      change,
            "change_pct":  chg_pct,
            "vwap":        round(vwap, 2),
            "above_vwap":  above_vwap,
            "above_ema9":  above_ema9,
            "trending_up": trending_up,
            "message":     message,
            "timestamp":   datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"status": "unknown", "message": str(e)})

# ── FEATURE 4: EOD RESULTS + PATTERN RECOGNITION ──────────────────────────────

@app.route('/api/eod-results')
def get_eod_results():
    """EOD results with force-refresh support."""
    from flask import request
    force = request.args.get("force", "false").lower() == "true"
    EOD_FILE = "eod_results.json"

    if not force:
        cached = load_file(EOD_FILE)
        if cached and cached.get("date") == datetime.now().strftime("%Y-%m-%d"):
            return jsonify(cached)

    morning = load_file("morning_golist.json")
    if not morning or not morning.get("golist"):
        return jsonify({"error": "No morning go-list found. Run morning scan first.", "results": []})

    results = []
    total_pnl = 0
    wins = losses = 0

    for stock in morning["golist"]:
        sym   = stock["symbol"]
        entry = (stock.get("trade_levels") or {}).get("entry") or stock.get("prev_close")
        if not entry:
            continue
        try:
            ticker = yf.Ticker(sym)
            df = ticker.history(period="2d", interval="1d")
            if df is None or len(df) < 1:
                continue

            today_close  = round(float(df['Close'].iloc[-1]), 2)
            today_open   = round(float(df['Open'].iloc[-1]),  2)
            today_high   = round(float(df['High'].iloc[-1]),  2)
            today_low    = round(float(df['Low'].iloc[-1]),   2)
            today_vol    = int(df['Volume'].iloc[-1])
            pnl_pct      = round(((today_close - entry) / entry) * 100, 2)
            pnl_dollar   = round(today_close - entry, 2)

            tl       = stock.get("trade_levels") or {}
            t1_hit   = bool(tl.get("target1") and today_high >= tl["target1"])
            t2_hit   = bool(tl.get("target2") and today_high >= tl["target2"])
            t3_hit   = bool(tl.get("target3") and today_high >= tl["target3"])
            stop_hit = bool(tl.get("stop")    and today_low  <= tl["stop"])

            outcome = "WIN" if pnl_pct > 0.5 else "LOSS" if pnl_pct < -0.5 else "FLAT"
            if outcome == "WIN":  wins   += 1
            if outcome == "LOSS": losses += 1
            total_pnl += pnl_pct

            results.append({
                "symbol":       sym,
                "grade":        stock.get("grade", "C"),
                "sector":       stock.get("sector_etf", ""),
                "entry":        round(float(entry), 2),
                "close":        today_close,
                "open":         today_open,
                "high":         today_high,
                "low":          today_low,
                "volume":       today_vol,
                "pnl_pct":      pnl_pct,
                "pnl_dollar":   pnl_dollar,
                "outcome":      outcome,
                "t1_hit":       t1_hit,
                "t2_hit":       t2_hit,
                "t3_hit":       t3_hit,
                "stop_hit":     stop_hit,
                "target1":      tl.get("target1"),
                "target2":      tl.get("target2"),
                "target3":      tl.get("target3"),
                "stop":         tl.get("stop"),
                "pm_change":    stock.get("pm_change", 0),
                "evening_score": stock.get("evening_score", 0),
                "rvol":         stock.get("rvol", 0),
                "gap_pct":      stock.get("gap_pct", 0),
            })
        except Exception as e:
            print(f"EOD error {sym}: {e}")

    results.sort(key=lambda x: x["pnl_pct"], reverse=True)
    avg_pnl = round(total_pnl / len(results), 2) if results else 0
    output = {
        "date":      datetime.now().strftime("%Y-%m-%d"),
        "timestamp": datetime.now().isoformat(),
        "results":   results,
        "summary": {
            "total":     len(results),
            "wins":      wins,
            "losses":    losses,
            "flat":      len(results) - wins - losses,
            "win_rate":  round((wins / len(results)) * 100, 1) if results else 0,
            "avg_pnl":   avg_pnl,
            "total_pnl": round(total_pnl, 2),
        }
    }
    save_file(EOD_FILE, output)
    return jsonify(output)

@app.route('/api/eod-history')
def get_eod_history():
    history = load_file("eod_history.json") or []
    return jsonify(history)

# ── FEATURE 4 cont: PATTERN RECOGNITION ──────────────────────────────────────

@app.route('/api/patterns')
def get_patterns():
    """Analyze EOD history to find YOUR personal edge patterns."""
    history = load_file("eod_history.json") or []
    if len(history) < 3:
        return jsonify({"error": "Need at least 3 days of EOD history for pattern analysis.", "patterns": []})

    all_trades = []
    for day in history:
        for r in day.get("results", []):
            r["date"] = day.get("date", "")
            all_trades.append(r)

    if not all_trades:
        return jsonify({"error": "No trades in history yet.", "patterns": []})

    patterns = []

    # ── By Grade ──
    for grade in ["A", "B", "C"]:
        trades = [t for t in all_trades if t.get("grade") == grade]
        if len(trades) >= 3:
            wr, avg = _win_stats(trades)
            patterns.append({
                "label":      f"Grade {grade} Setups",
                "type":       "grade",
                "key":        grade,
                "count":      len(trades),
                "win_rate":   wr,
                "avg_pnl":    avg,
                "insight":    _insight(wr, avg, f"Grade {grade}"),
                "color":      "green" if wr > 55 else "red" if wr < 45 else "yellow"
            })

    # ── By Day of Week ──
    for dow, name in enumerate(["Monday","Tuesday","Wednesday","Thursday","Friday"]):
        trades = [t for t in all_trades if _dow(t.get("date","")) == dow]
        if len(trades) >= 3:
            wr, avg = _win_stats(trades)
            patterns.append({
                "label":    name,
                "type":     "day",
                "key":      name,
                "count":    len(trades),
                "win_rate": wr,
                "avg_pnl":  avg,
                "insight":  _insight(wr, avg, name),
                "color":    "green" if wr > 55 else "red" if wr < 45 else "yellow"
            })

    # ── By RVOL bucket ──
    for label, lo, hi in [("Low RVOL (<1.5x)",0,1.5),("Med RVOL (1.5–3x)",1.5,3),("High RVOL (>3x)",3,99)]:
        trades = [t for t in all_trades if lo <= t.get("rvol",0) < hi]
        if len(trades) >= 3:
            wr, avg = _win_stats(trades)
            patterns.append({
                "label":    label,
                "type":     "rvol",
                "key":      label,
                "count":    len(trades),
                "win_rate": wr,
                "avg_pnl":  avg,
                "insight":  _insight(wr, avg, label),
                "color":    "green" if wr > 55 else "red" if wr < 45 else "yellow"
            })

    # ── By Gap size ──
    for label, lo, hi in [("Small Gap (<2%)",0,2),("Ideal Gap (2–8%)",2,8),("Extended Gap (>8%)",8,99)]:
        trades = [t for t in all_trades if lo <= abs(t.get("gap_pct",0)) < hi]
        if len(trades) >= 3:
            wr, avg = _win_stats(trades)
            patterns.append({
                "label":    label,
                "type":     "gap",
                "key":      label,
                "count":    len(trades),
                "win_rate": wr,
                "avg_pnl":  avg,
                "insight":  _insight(wr, avg, label),
                "color":    "green" if wr > 55 else "red" if wr < 45 else "yellow"
            })

    # Sort by win rate descending
    patterns.sort(key=lambda x: x["win_rate"], reverse=True)

    # Best and worst setups
    best  = [p for p in patterns if p["win_rate"] >= 60][:3]
    worst = [p for p in patterns if p["win_rate"] <= 40][:3]

    return jsonify({
        "patterns":    patterns,
        "best":        best,
        "worst":       worst,
        "total_trades": len(all_trades),
        "days_tracked": len(history),
        "timestamp":   datetime.now().isoformat()
    })

def _win_stats(trades):
    wins = sum(1 for t in trades if t.get("outcome") == "WIN")
    wr   = round((wins / len(trades)) * 100, 1)
    avg  = round(sum(t.get("pnl_pct", 0) for t in trades) / len(trades), 2)
    return wr, avg

def _dow(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").weekday()
    except:
        return -1

def _insight(wr, avg, label):
    if wr >= 65 and avg > 1:
        return f"Strong edge on {label} — prioritize these setups"
    elif wr >= 55:
        return f"{label} is working well — keep taking these"
    elif wr <= 40 and avg < 0:
        return f"Avoid {label} — historically poor results for you"
    elif wr <= 45:
        return f"{label} underperforming — reduce size or skip"
    else:
        return f"{label} is neutral — no clear edge yet"

# ── FEATURE 8: WEEKLY JOURNAL ─────────────────────────────────────────────────

@app.route('/api/weekly-journal')
def weekly_journal():
    """Auto-generated weekly performance summary from EOD history."""
    history = load_file("eod_history.json") or []
    if not history:
        return jsonify({"error": "No history yet. EOD results build up over time.", "weeks": []})

    # Group by week
    weeks = {}
    for day in history:
        try:
            d = datetime.strptime(day["date"], "%Y-%m-%d")
            # Week key = Monday of that week
            monday = d - timedelta(days=d.weekday())
            wk = monday.strftime("%Y-%m-%d")
            if wk not in weeks:
                weeks[wk] = []
            weeks[wk].append(day)
        except:
            continue

    result_weeks = []
    for wk_start, days in sorted(weeks.items(), reverse=True)[:8]:  # last 8 weeks
        all_trades = []
        for d in days:
            for r in d.get("results", []):
                all_trades.append(r)

        if not all_trades:
            continue

        wins   = sum(1 for t in all_trades if t.get("outcome") == "WIN")
        losses = sum(1 for t in all_trades if t.get("outcome") == "LOSS")
        total  = len(all_trades)
        wr     = round((wins / total) * 100, 1) if total else 0
        avg_pnl = round(sum(t.get("pnl_pct",0) for t in all_trades) / total, 2) if total else 0
        total_pnl = round(sum(t.get("pnl_pct",0) for t in all_trades), 2)
        best_trade  = max(all_trades, key=lambda t: t.get("pnl_pct",0))
        worst_trade = min(all_trades, key=lambda t: t.get("pnl_pct",0))

        # Grade breakdown
        by_grade = {}
        for g in ["A","B","C"]:
            gt = [t for t in all_trades if t.get("grade") == g]
            if gt:
                gw = sum(1 for t in gt if t.get("outcome") == "WIN")
                by_grade[g] = {"count": len(gt), "wins": gw, "win_rate": round(gw/len(gt)*100,1)}

        # Most traded sectors
        sectors = {}
        for t in all_trades:
            s = t.get("sector","Unknown") or "Unknown"
            sectors[s] = sectors.get(s,0) + 1
        top_sectors = sorted(sectors.items(), key=lambda x: x[1], reverse=True)[:3]

        wk_end = (datetime.strptime(wk_start,"%Y-%m-%d") + timedelta(days=4)).strftime("%Y-%m-%d")

        result_weeks.append({
            "week_start":  wk_start,
            "week_end":    wk_end,
            "days_traded": len(days),
            "total_trades": total,
            "wins":        wins,
            "losses":      losses,
            "win_rate":    wr,
            "avg_pnl":     avg_pnl,
            "total_pnl":   total_pnl,
            "by_grade":    by_grade,
            "top_sectors": [{"sector": s, "count": c} for s,c in top_sectors],
            "best_trade":  {"symbol": best_trade["symbol"],  "pnl": best_trade["pnl_pct"]},
            "worst_trade": {"symbol": worst_trade["symbol"], "pnl": worst_trade["pnl_pct"]},
            "grade": "A" if wr >= 60 and avg_pnl > 1 else "B" if wr >= 50 else "C",
        })

    return jsonify({"weeks": result_weeks, "timestamp": datetime.now().isoformat()})

# ── EXIT MANAGER QUOTE ────────────────────────────────────────────────────────

@app.route('/api/quote/<symbol>')
def get_quote(symbol):
    try:
        sym    = symbol.upper().strip()
        ticker = yf.Ticker(sym)
        df     = ticker.history(period="60d")
        if df is None or len(df) < 2:
            return jsonify({"error": f"No data found for {sym}"}), 404

        closes   = df['Close'].tolist()
        highs    = df['High'].tolist()
        lows     = df['Low'].tolist()
        price    = round(float(closes[-1]), 2)
        prev     = round(float(closes[-2]), 2)
        change   = round(price - prev, 2)
        chg_pct  = round(((price - prev) / prev) * 100, 2)
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
            "symbol": sym, "name": name, "price": price,
            "prev_close": prev, "change": change, "change_pct": chg_pct,
            "atr": atr, "atr_pct": atr_pct,
            "high_52w": high_52w, "low_52w": low_52w, "avg_volume": avg_vol,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── STATUS ────────────────────────────────────────────────────────────────────

@app.route('/api/status')
def status():
    return jsonify({
        "status": "running",
        "version": "5.0",
        "time": datetime.now().isoformat(),
        "has_results": latest_results is not None
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
