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

from werkzeug.middleware.proxy_fix import ProxyFix
from flask import Flask, jsonify, Response, request, session, redirect, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import run_scanner, run_morning_scan, run_backtest
from functools import wraps
import json, os, threading, statistics, hashlib, secrets
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

# Google Sheets integration (optional — only active if credentials are configured)
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except ImportError:
    GSHEETS_AVAILABLE = False
    print("[SHEETS] gspread not installed — Google Sheets sync disabled")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.secret_key = os.environ.get('SECRET_KEY', 'dayedge-secret-key-2026-xK9mP2vL7n')
app.config['SESSION_COOKIE_SECURE']      = os.environ.get('RAILWAY_ENVIRONMENT') is not None
app.config['SESSION_COOKIE_HTTPONLY']    = True
app.config['SESSION_COOKIE_SAMESITE']   = 'Lax'
app.config['SESSION_COOKIE_NAME']       = 'dayedge_v5_session'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

latest_results = None
latest_morning = None
scan_status = {"running": False, "task": None, "started": None, "error": None}

# ── AUTH ──────────────────────────────────────────────────────────────────────

def hash_pw(pw):
    return hashlib.sha256(pw.encode('utf-8')).hexdigest()

def get_users():
    """Always read from env so Railway variable updates take effect immediately."""
    admin_user = os.environ.get('ADMIN_USER', 'admin').strip().lower()
    admin_pass = os.environ.get('ADMIN_PASS', 'dayedge_admin_2026').strip()
    user_user  = os.environ.get('USER_USER',  'trader').strip().lower()
    user_pass  = os.environ.get('USER_PASS',  'dayedge_trader_2026').strip()
    print(f"[AUTH] Users configured: admin_user={admin_user!r}, user_user={user_user!r}")
    return {
        admin_user: {'password_hash': hash_pw(admin_pass), 'role': 'admin'},
        user_user:  {'password_hash': hash_pw(user_pass),  'role': 'user'},
    }

ROLE_ACCESS = {
    'admin': ['watchlist','morning','sectors','backtest','exit','live','premarket','risk','eod','patterns','journal'],
    'user':  ['watchlist','morning','sectors','backtest','exit','live','premarket','risk']
}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Not authenticated', 'redirect': '/login'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            return jsonify({'error': 'Not authenticated', 'redirect': '/login'}), 401
        if session.get('role') != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated

# ── PERSISTENT DATA DIRECTORY ────────────────────────────────────────────────
# Uses /data/ when Railway Volume is mounted at /data
# Falls back to current directory for local development
DATA_DIR = os.environ.get("DATA_DIR", "/data" if os.path.exists("/data") else ".")
os.makedirs(DATA_DIR, exist_ok=True)
print(f"[STORAGE] Data directory: {DATA_DIR}")

def data_path(filename):
    """Return full path to a data file in the persistent directory."""
    return os.path.join(DATA_DIR, filename)

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

# ── GOOGLE SHEETS SYNC ───────────────────────────────────────────────────────

# ── EVENING SCAN SHEET (for persistence across Railway restarts) ─────────────
EVENING_SHEET_NAME  = "Evening Scan"
MORNING_SHEET_NAME  = "Morning Go-List"
PERSISTENCE_SHEET   = "Last Scan"   # single-row sheet storing raw JSON for recovery

SHEET_COLUMNS = [
    "Date", "Symbol", "Grade", "Evening Score", "Prev Close",
    "PM Change %", "PM Volume %", "Entry", "Stop", "Stop %",
    "Target 1", "Target 2", "Target 3", "ATR",
    "Sector", "RVOL", "Gap %", "ADX", "Float M",
    "R/R Ratio", "Unusual Options", "Has Catalyst",
    "Short Float %", "Squeeze Score", "Inst Score",
    "Best Window", "Scan Timestamp"
]

def get_sheets_client():
    """Build authenticated Google Sheets client from env var or credentials file."""
    if not GSHEETS_AVAILABLE:
        return None
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        # Option 1: credentials JSON stored as env var (recommended for Railway)
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if creds_json:
            import json as _json
            creds_dict = _json.loads(creds_json)
            creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        # Option 2: credentials file on disk
        elif os.path.exists("google_credentials.json"):
            creds = Credentials.from_service_account_file("google_credentials.json", scopes=scopes)
        else:
            print("[SHEETS] No credentials found — set GOOGLE_CREDENTIALS_JSON env var")
            return None
        return gspread.authorize(creds)
    except Exception as e:
        print(f"[SHEETS] Auth error: {e}")
        return None

def get_or_create_sheet(client, spreadsheet_id=None):
    """Open existing sheet by ID or create a new one named DayEdge Journal."""
    try:
        sheet_id = spreadsheet_id or os.environ.get("GOOGLE_SHEET_ID")
        if sheet_id:
            return client.open_by_key(sheet_id).sheet1
        else:
            # Create new spreadsheet
            sh = client.create("DayEdge Morning Journal")
            sh.share(None, perm_type='anyone', role='reader')  # anyone with link can view
            print(f"[SHEETS] Created new sheet: {sh.url}")
            # Save the ID so we reuse it
            save_file(data_path("sheet_id.json"), {"id": sh.id, "url": sh.url})
            ws = sh.sheet1
            ws.update_title("Morning Go-List")
            return ws
    except Exception as e:
        print(f"[SHEETS] Open/create error: {e}")
        return None

def ensure_sheet_header(ws):
    """Make sure row 1 has the correct column headers."""
    try:
        first_row = ws.row_values(1)
        if first_row != SHEET_COLUMNS:
            ws.insert_row(SHEET_COLUMNS, index=1)
            # Format header row bold
            ws.format("A1:AA1", {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.05, "green": 0.07, "blue": 0.1}
            })
            print("[SHEETS] Header row written")
    except Exception as e:
        print(f"[SHEETS] Header error: {e}")

def save_scan_to_sheets(scan_data):
    """Save evening scan results to Google Sheets for persistence across restarts."""
    if not GSHEETS_AVAILABLE: return
    client = get_sheets_client()
    if not client: return
    try:
        sheet_id = os.environ.get("GOOGLE_SHEET_ID")
        saved    = load_file(data_path("sheet_id.json")) or {}
        sid      = sheet_id or saved.get("id")
        if not sid: return

        sh = client.open_by_key(sid)

        # Get or create "Last Scan" worksheet
        try:
            ws = sh.worksheet(PERSISTENCE_SHEET)
            ws.clear()
        except:
            ws = sh.add_worksheet(title=PERSISTENCE_SHEET, rows=5, cols=2)

        # Store as two cells: timestamp and full JSON
        import json as _json
        ts   = scan_data.get("timestamp", datetime.now().isoformat())
        data = _json.dumps(scan_data)
        ws.update("A1", [["timestamp", ts], ["data", data]])
        print(f"[SHEETS] Evening scan saved for persistence ({len(scan_data.get('results',[]))} stocks)")
    except Exception as e:
        print(f"[SHEETS] Save scan error: {e}")


def restore_scan_from_sheets():
    """Restore last evening scan from Google Sheets if local file is missing."""
    if not GSHEETS_AVAILABLE: return None
    client = get_sheets_client()
    if not client: return None
    try:
        sheet_id = os.environ.get("GOOGLE_SHEET_ID")
        saved    = load_file(data_path("sheet_id.json")) or {}
        sid      = sheet_id or saved.get("id")
        if not sid: return None

        sh = client.open_by_key(sid)
        try:
            ws   = sh.worksheet(PERSISTENCE_SHEET)
            rows = ws.get_all_values()
        except:
            return None

        # Find the data row
        import json as _json
        for row in rows:
            if len(row) >= 2 and row[0] == "data":
                data = _json.loads(row[1])
                print(f"[SHEETS] Restored evening scan from sheets: {len(data.get('results',[]))} stocks, ts={data.get('timestamp','?')}")
                # Re-save locally so scanner can use it
                save_file(data_path("scan_results.json"), data)
                return data
        return None
    except Exception as e:
        print(f"[SHEETS] Restore error: {e}")
        return None


def sync_morning_to_sheets(golist_data):
    """Push morning go-list to Google Sheets. Skips duplicates (same date+symbol)."""
    if not GSHEETS_AVAILABLE:
        return {"error": "gspread not installed on server"}

    client = get_sheets_client()
    if not client:
        return {"error": "Google Sheets not configured — add GOOGLE_CREDENTIALS_JSON to Railway variables"}

    ws = get_or_create_sheet(client)
    if not ws:
        return {"error": "Could not open or create Google Sheet"}

    ensure_sheet_header(ws)

    golist   = golist_data.get("golist", [])
    scan_ts  = golist_data.get("timestamp", datetime.now().isoformat())
    today    = datetime.now().strftime("%Y-%m-%d")

    if not golist:
        return {"error": "No stocks in go-list to sync"}

    # Fetch all existing rows to check for duplicates
    try:
        existing_rows = ws.get_all_values()
        # Build set of existing date+symbol combos (skip header row)
        existing_keys = set()
        for row in existing_rows[1:]:
            if len(row) >= 2:
                existing_keys.add(f"{row[0]}_{row[1]}")
    except Exception as e:
        print(f"[SHEETS] Read existing error: {e}")
        existing_keys = set()

    rows_to_add = []
    skipped = 0

    for s in golist:
        sym = s.get("symbol", "")
        key = f"{today}_{sym}"

        if key in existing_keys:
            skipped += 1
            continue

        tl = s.get("trade_levels") or {}
        ev = load_file(data_path("scan_results.json")) or {}
        ev_results = {r["symbol"]: r for r in ev.get("results", [])}
        ev_data = ev_results.get(sym, {})

        row = [
            today,
            sym,
            s.get("grade", ""),
            s.get("evening_score", ""),
            s.get("prev_close", ""),
            s.get("pm_change", ""),
            s.get("pm_vol_pct", ""),
            tl.get("entry", ""),
            tl.get("stop", ""),
            tl.get("stop_pct", ""),
            tl.get("target1", ""),
            tl.get("target2", ""),
            tl.get("target3", ""),
            tl.get("atr", ""),
            ev_data.get("sector_etf", s.get("sector_etf", "")),
            ev_data.get("rvol", ""),
            ev_data.get("gap_pct", ""),
            ev_data.get("adx", ""),
            ev_data.get("float_m", ""),
            ev_data.get("rr_ratio", ""),
            "Yes" if ev_data.get("unusual_options") else "No",
            "Yes" if ev_data.get("has_catalyst") else "No",
            ev_data.get("short_float_pct", ""),
            ev_data.get("short_squeeze_score", ""),
            ev_data.get("institutional_score", ""),
            s.get("best_window", ""),
            scan_ts,
        ]
        rows_to_add.append(row)

    if rows_to_add:
        # Batch append all new rows at once
        ws.append_rows(rows_to_add, value_input_option="USER_ENTERED")
        print(f"[SHEETS] Appended {len(rows_to_add)} rows, skipped {skipped} duplicates")

    # Get the sheet URL for the response
    try:
        sheet_url = f"https://docs.google.com/spreadsheets/d/{ws.spreadsheet.id}"
    except:
        saved = load_file(data_path("sheet_id.json")) or {}
        sheet_url = saved.get("url", "")

    return {
        "ok":       True,
        "added":    len(rows_to_add),
        "skipped":  skipped,
        "total":    len(golist),
        "date":     today,
        "sheet_url": sheet_url
    }

# ── BACKGROUND TASKS ─────────────────────────────────────────────────────────

def run_scan_background():
    global latest_results, scan_status
    try:
        scan_status["running"] = True
        scan_status["error"] = None
        latest_results = run_scanner()
        if latest_results and (os.environ.get("GOOGLE_CREDENTIALS_JSON") or os.path.exists("google_credentials.json")):
            try:
                save_scan_to_sheets(latest_results)
            except Exception as se:
                print(f"[SHEETS] Evening persistence save error: {se}")
    except Exception as e:
        scan_status["error"] = str(e)
        print(f"Background scan error: {e}")
    finally:
        scan_status["running"] = False

def run_morning_background():
    global latest_morning, latest_results, scan_status
    try:
        scan_status["running"] = True
        scan_status["error"] = None
        # Ensure evening scan results exist before running morning scan
        if not load_file(data_path("scan_results.json")):
            print("[MORNING] scan_results.json missing — attempting restore from Sheets...")
            restored = restore_scan_from_sheets()
            if restored:
                print(f"[MORNING] Restored {len(restored.get('results',[]))} stocks — morning scan can proceed")
                latest_results = restored
            else:
                print("[MORNING] No evening scan data found — morning scan may return empty list")
        latest_morning = run_morning_scan()
        # Auto-sync to Google Sheets if configured (admin feature)
        if latest_morning and os.environ.get("GOOGLE_CREDENTIALS_JSON") or os.path.exists("google_credentials.json"):
            try:
                result = sync_morning_to_sheets(latest_morning)
                if result.get("ok"):
                    print(f"[SHEETS] Auto-sync: {result['added']} rows added, {result['skipped']} skipped")
                else:
                    print(f"[SHEETS] Auto-sync skipped: {result.get('error','')}")
            except Exception as se:
                print(f"[SHEETS] Auto-sync error: {se}")
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
        eod = load_file(data_path("eod_results.json"))
        if eod and eod.get("date") == datetime.now().strftime("%Y-%m-%d"):
            history = load_file(data_path("eod_history.json")) or []
            # Avoid duplicate dates
            history = [h for h in history if h.get("date") != eod["date"]]
            history.append(eod)
            history = history[-60:]  # keep 60 days
            save_file(data_path("eod_history.json"), history)
            print("[SCHEDULER] EOD history saved")
    except Exception as e:
        print(f"[SCHEDULER] EOD save error: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_evening,  'cron', day_of_week='mon-fri', hour=18, minute=0)
scheduler.add_job(scheduled_morning,  'cron', day_of_week='mon-fri', hour=9,  minute=0)
scheduler.add_job(scheduled_eod_save, 'cron', day_of_week='mon-fri', hour=16, minute=15)
scheduler.start()

# ── CORE ROUTES ───────────────────────────────────────────────────────────────

# ── AUTH ROUTES ──────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET'])
def login_page():
    if 'username' in session:
        return redirect('/')
    return send_from_directory('static', 'login.html')

@app.route('/api/login', methods=['POST'])
def do_login():
    data     = request.get_json() or {}
    username = data.get('username', '').strip().lower()
    password = data.get('password', '').strip()

    print(f"[LOGIN] Attempt: username={username!r}")

    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400

    users    = get_users()
    user     = users.get(username)
    pw_hash  = hash_pw(password)

    print(f"[LOGIN] Known users: {list(users.keys())}")
    print(f"[LOGIN] User found: {user is not None}")
    if user:
        print(f"[LOGIN] Hash match: {user['password_hash'] == pw_hash}")

    if not user or user['password_hash'] != pw_hash:
        return jsonify({'error': 'Invalid username or password'}), 401

    # Set session
    session.clear()
    session.permanent = True
    session['username'] = username
    session['role']     = user['role']

    print(f"[LOGIN] Success: {username} role={user['role']} session_id={request.cookies.get('dayedge_session','none')}")

    return jsonify({
        'ok':       True,
        'username': username,
        'role':     user['role'],
        'access':   ROLE_ACCESS[user['role']]
    })

@app.route('/api/logout', methods=['GET', 'POST'])
def do_logout():
    session.clear()
    if request.method == 'GET':
        return redirect('/login')
    return jsonify({'ok': True})

@app.route('/api/me')
def me():
    if 'username' not in session:
        return jsonify({'authenticated': False}), 401
    return jsonify({
        'authenticated': True,
        'username': session['username'],
        'role':     session['role'],
        'access':   ROLE_ACCESS[session['role']]
    })

@app.route('/')
@login_required
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/scan')
@login_required
def get_scan():
    global latest_results
    if latest_results is None:
        latest_results = load_file(data_path("scan_results.json"))
    if latest_results is None:
        try:
            latest_results = restore_scan_from_sheets()
            if latest_results:
                print(f"[STARTUP] Restored {len(latest_results.get('results',[]))} stocks from Google Sheets")
        except Exception as _e:
            print(f"[STARTUP] Restore failed: {_e}")
    if latest_results is None:
        return jsonify({"error": "No scan results yet. Click Run Scan Now.", "results": []})
    return jsonify(make_serializable(latest_results))

@app.route('/api/morning')
@login_required
def get_morning():
    global latest_morning
    if latest_morning is None:
        latest_morning = load_file(data_path("morning_golist.json"))
    if latest_morning is None:
        return jsonify({"golist": [], "message": "No morning scan yet."})
    return jsonify(make_serializable(latest_morning))

@app.route('/api/backtest')
@login_required
def get_backtest():
    data = load_file(data_path("backtest_results.json"))
    if data is None:
        return jsonify({"error": "No backtest run yet."})
    return jsonify(make_serializable(data))

@app.route('/api/scan-status')
@login_required
def get_scan_status():
    return jsonify({
        "running":     scan_status["running"],
        "task":        scan_status["task"],
        "error":       scan_status["error"],
        "has_results": latest_results is not None
    })

@app.route('/api/run-scan', methods=['POST'])
@login_required
def trigger_scan():
    global scan_status
    if scan_status["running"]:
        return jsonify({"status": "already_running"})
    scan_status["task"] = "evening"
    scan_status["started"] = datetime.now().isoformat()
    threading.Thread(target=run_scan_background, daemon=True).start()
    return jsonify({"status": "started"})

@app.route('/api/run-morning', methods=['POST'])
@login_required
def trigger_morning():
    global scan_status
    if scan_status["running"]:
        return jsonify({"status": "already_running"})
    scan_status["task"] = "morning"
    threading.Thread(target=run_morning_background, daemon=True).start()
    return jsonify({"status": "started"})

@app.route('/api/run-backtest', methods=['POST'])
@login_required
def trigger_backtest():
    global scan_status
    if scan_status["running"]:
        return jsonify({"status": "already_running"})
    scan_status["task"] = "backtest"
    threading.Thread(target=run_backtest_background, daemon=True).start()
    return jsonify({"status": "started"})

# ── FEATURE 1: LIVE INTRADAY TRACKER ─────────────────────────────────────────

@app.route('/api/live-tracker')
@login_required
def live_tracker():
    """Real-time prices for morning go-list. Shows P&L vs entry and target zones."""
    morning = load_file(data_path("morning_golist.json"))
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
@login_required
def premarket_momentum():
    """Pre-market momentum ranker — volume surge and price acceleration."""
    morning = load_file(data_path("morning_golist.json"))
    golist  = (morning or {}).get("golist", [])

    # Also check evening scan for any high-scorers not in morning list
    evening = load_file(data_path("scan_results.json"))
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
@login_required
def risk_dashboard():
    """Pre-market risk summary: total exposure, max loss, position sizing guide."""
    morning = load_file(data_path("morning_golist.json"))
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
@login_required
def spy_condition():
    """Live SPY + QQQ trend — green/yellow/red signal for intraday."""
    def get_index_data(symbol):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="1d", interval="5m")
            if df is None or len(df) < 5:
                return None

            # Check if market is actually open — last bar must be recent
            from datetime import timezone
            last_bar_time = df.index[-1]
            if hasattr(last_bar_time, 'tzinfo') and last_bar_time.tzinfo is not None:
                now_utc = datetime.now(timezone.utc)
                minutes_since_last_bar = (now_utc - last_bar_time).total_seconds() / 60
            else:
                minutes_since_last_bar = 0

            market_open = minutes_since_last_bar < 15

            price   = round(float(df['Close'].iloc[-1]), 2)
            open_   = round(float(df['Open'].iloc[0]),  2)
            high    = round(float(df['High'].max()), 2)
            low     = round(float(df['Low'].min()),  2)
            change  = round(price - open_, 2)
            chg_pct = round((change / open_) * 100, 2)

            ema9       = float(df['Close'].ewm(span=9).mean().iloc[-1])
            above_ema9 = price > ema9

            typical    = df[['High','Low','Close']].mean(axis=1)
            vwap       = float((typical * df['Volume']).cumsum().iloc[-1] / df['Volume'].cumsum().iloc[-1])
            above_vwap = price > vwap

            last3       = df['Close'].tail(3).tolist()
            trending_up = last3[-1] > last3[0]

            if not market_open:
                status  = "closed"
                message = f"{symbol} market closed"
            elif chg_pct > 0.3 and above_vwap and above_ema9:
                status  = "bullish"
                message = f"{symbol} trending up — good for longs"
            elif chg_pct < -0.3 and not above_vwap:
                status  = "bearish"
                message = f"{symbol} selling off — avoid new longs"
            else:
                status  = "neutral"
                message = f"{symbol} choppy — be selective"

            return {
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
                "market_open": market_open,
                "message":     message,
            }
        except Exception as e:
            return {"status": "unknown", "message": str(e)}

    try:
        spy_data = get_index_data("SPY")
        qqq_data = get_index_data("QQQ")

        # Overall market condition based on both
        spy_status = spy_data["status"] if spy_data else "unknown"
        qqq_status = qqq_data["status"] if qqq_data else "unknown"

        if spy_status == "closed" or qqq_status == "closed":
            overall = "closed"
        elif spy_status == "bullish" and qqq_status == "bullish":
            overall = "bullish"
        elif spy_status == "bearish" or qqq_status == "bearish":
            overall = "bearish"
        else:
            overall = "neutral"

        return jsonify({
            "status":    overall,
            "spy":       spy_data,
            "qqq":       qqq_data,
            "message":   f"SPY {spy_status.upper()} · QQQ {qqq_status.upper()}",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"status": "unknown", "message": str(e)})

# ── FEATURE 4: EOD RESULTS + PATTERN RECOGNITION ──────────────────────────────

@app.route('/api/eod-results')
@admin_required
def get_eod_results():
    """EOD results with force-refresh support."""
    from flask import request
    force = request.args.get("force", "false").lower() == "true"
    EOD_FILE = data_path("eod_results.json")

    if not force:
        cached = load_file(EOD_FILE)
        if cached and cached.get("date") == datetime.now().strftime("%Y-%m-%d"):
            return jsonify(cached)

    morning = load_file(data_path("morning_golist.json"))
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

    # Load trade log so we know which the user actually traded
    trade_log = load_trade_log()
    today = datetime.now().strftime("%Y-%m-%d")
    for r in results:
        key = f"{today}_{r['symbol']}"
        log_entry = trade_log.get(key, {})
        r["traded"]        = log_entry.get("traded", None)   # None = not set yet
        r["actual_shares"] = log_entry.get("shares", 0)
        r["actual_entry"]  = log_entry.get("entry", 0)

    # Separate traded vs not-traded for summary
    traded_results = [r for r in results if r.get("traded") is True]
    tw = sum(1 for r in traded_results if r.get("outcome") == "WIN")
    tl_count = sum(1 for r in traded_results if r.get("outcome") == "LOSS")
    t_avg = round(sum(r["pnl_pct"] for r in traded_results) / len(traded_results), 2) if traded_results else 0

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
            # Traded-only summary
            "traded_count":   len(traded_results),
            "traded_wins":    tw,
            "traded_losses":  tl_count,
            "traded_win_rate": round((tw / len(traded_results)) * 100, 1) if traded_results else 0,
            "traded_avg_pnl": t_avg,
        }
    }
    save_file(EOD_FILE, output)

    # ── Auto-save to history every time EOD is refreshed ──────────────────
    try:
        history = load_file(data_path("eod_history.json")) or []
        history = [h for h in history if h.get("date") != output["date"]]
        history.append(output)
        history = sorted(history, key=lambda x: x.get("date",""))[-60:]
        save_file(data_path("eod_history.json"), history)
    except Exception as e:
        print(f"History auto-save error: {e}")

    return jsonify(output)

@app.route('/api/eod-history')
@admin_required
def get_eod_history():
    history = load_file(data_path("eod_history.json")) or []
    return jsonify(history)

@app.route('/api/save-eod-history', methods=['POST'])
@admin_required
def save_eod_history():
    """Manually save today's EOD results into the history file.
    Called by the frontend when the user clicks 'Save to History'.
    Also called automatically whenever EOD results are refreshed.
    """
    try:
        eod = load_file(data_path("eod_results.json"))
        if not eod:
            return jsonify({"error": "No EOD results to save. Refresh EOD Results first."}), 400

        history = load_file(data_path("eod_history.json")) or []
        # Replace existing entry for same date (idempotent)
        history = [h for h in history if h.get("date") != eod.get("date")]
        history.append(eod)
        history = sorted(history, key=lambda x: x.get("date",""))[-60:]  # keep last 60 days
        save_file(data_path("eod_history.json"), history)
        return jsonify({"ok": True, "days_in_history": len(history), "date": eod.get("date")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── TRADE LOG — track which stocks user actually traded ───────────────────────

TRADE_LOG_FILE = data_path("trade_log.json")

def load_trade_log():
    return load_file(TRADE_LOG_FILE) or {}

def save_trade_log(log):
    save_file(TRADE_LOG_FILE, log)

@app.route('/api/trade-log', methods=['GET'])
@login_required
def get_trade_log():
    """Return the full trade log keyed by date+symbol."""
    return jsonify(load_trade_log())

@app.route('/api/trade-log', methods=['POST'])
@login_required
def update_trade_log():
    """Toggle a trade as TRADED or SKIPPED for a given date+symbol.
    Body: { "symbol": "NVDA", "date": "2026-02-23", "traded": true }
    """
    from flask import request
    try:
        body   = request.get_json()
        symbol = body.get("symbol", "").upper().strip()
        date   = body.get("date", datetime.now().strftime("%Y-%m-%d"))
        traded = bool(body.get("traded", True))
        shares = int(body.get("shares", 0))
        entry  = float(body.get("entry", 0))

        if not symbol:
            return jsonify({"error": "Symbol required"}), 400

        log = load_trade_log()
        key = f"{date}_{symbol}"
        log[key] = {
            "symbol":  symbol,
            "date":    date,
            "traded":  traded,
            "shares":  shares,
            "entry":   entry,
            "updated": datetime.now().isoformat()
        }
        save_trade_log(log)

        # Also update eod_results.json so EOD tab reflects traded status
        eod = load_file(data_path("eod_results.json"))
        if eod and eod.get("date") == date:
            for r in eod.get("results", []):
                if r["symbol"] == symbol:
                    r["traded"] = traded
                    r["actual_shares"] = shares
                    r["actual_entry"]  = entry if entry > 0 else r.get("entry", 0)
            save_file(data_path("eod_results.json"), eod)

        return jsonify({"ok": True, "key": key, "traded": traded})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── FEATURE 4 cont: PATTERN RECOGNITION ──────────────────────────────────────

@app.route('/api/patterns')
@admin_required
def get_patterns():
    """Analyze EOD history to find YOUR personal edge patterns."""
    history = load_file(data_path("eod_history.json")) or []
    if len(history) < 3:
        return jsonify({"error": "Need at least 3 days of EOD history for pattern analysis.", "patterns": []})

    all_trades = []
    for day in history:
        for r in day.get("results", []):
            # Only count trades the user actually made (traded=True)
            # If traded is None (never set), include it anyway for backwards compat
            if r.get("traded") is False:
                continue
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
@admin_required
def weekly_journal():
    """Auto-generated weekly performance summary from EOD history."""
    history = load_file(data_path("eod_history.json")) or []
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
                # Only count trades the user actually made
                if r.get("traded") is False:
                    continue
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
@login_required
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

# ── GOOGLE SHEETS ROUTES ─────────────────────────────────────────────────────

@app.route('/api/sync-sheets', methods=['POST'])
@admin_required
def sync_sheets():
    """Manually push current morning go-list to Google Sheets."""
    morning = load_file(data_path("morning_golist.json"))
    if not morning or not morning.get("golist"):
        return jsonify({"error": "No morning go-list found. Run morning scan first."}), 400
    result = sync_morning_to_sheets(morning)
    if result.get("ok"):
        return jsonify(result)
    return jsonify(result), 500

@app.route('/api/sheet-status')
@admin_required
def sheet_status():
    """Check if Google Sheets is configured and return sheet URL if so."""
    configured = bool(
        GSHEETS_AVAILABLE and (
            os.environ.get("GOOGLE_CREDENTIALS_JSON") or
            os.path.exists("google_credentials.json")
        )
    )
    sheet_id   = os.environ.get("GOOGLE_SHEET_ID", "")
    saved      = load_file(data_path("sheet_id.json")) or {}
    sheet_url  = f"https://docs.google.com/spreadsheets/d/{sheet_id}" if sheet_id else saved.get("url", "")
    return jsonify({
        "configured":       configured,
        "gspread_installed": GSHEETS_AVAILABLE,
        "has_credentials":  bool(os.environ.get("GOOGLE_CREDENTIALS_JSON") or os.path.exists("google_credentials.json")),
        "has_sheet_id":     bool(sheet_id),
        "sheet_url":        sheet_url,
    })

@app.route('/api/debug-auth')
def debug_auth():
    """Shows auth config status — no passwords exposed, just confirms vars are set."""
    users = get_users()
    return jsonify({
        'ADMIN_USER_set':   bool(os.environ.get('ADMIN_USER')),
        'ADMIN_USER_value': os.environ.get('ADMIN_USER', '(using default: admin)'),
        'ADMIN_PASS_set':   bool(os.environ.get('ADMIN_PASS')),
        'USER_USER_set':    bool(os.environ.get('USER_USER')),
        'USER_USER_value':  os.environ.get('USER_USER', '(using default: trader)'),
        'USER_PASS_set':    bool(os.environ.get('USER_PASS')),
        'SECRET_KEY_set':   bool(os.environ.get('SECRET_KEY')),
        'known_usernames':  list(users.keys()),
        'session_active':   'username' in session,
        'session_user':     session.get('username', None),
    })

@app.route('/api/chart/<symbol>')
@login_required
def get_chart_data(symbol):
    """Return OHLCV intraday data for charting with VWAP + EMAs."""
    try:
        interval = request.args.get('interval', '5m')
        period   = request.args.get('period', '1d')

        # Validate interval
        valid_intervals = ['5m', '15m', '1h']
        if interval not in valid_intervals:
            interval = '5m'

        symbol = symbol.upper().strip()
        ticker = yf.Ticker(symbol)

        # Fetch with prepost for pre-market data
        df = ticker.history(period=period, interval=interval, prepost=True)

        if df is None or len(df) == 0:
            return jsonify({'error': f'No data found for {symbol}'}), 404

        # Calculate VWAP
        typical = (df['High'] + df['Low'] + df['Close']) / 3
        cum_tpv  = (typical * df['Volume']).cumsum()
        cum_vol  = df['Volume'].cumsum()
        vwap_series = (cum_tpv / cum_vol.replace(0, float('nan'))).fillna(method='ffill')

        # Calculate EMA9 and EMA20
        ema9  = df['Close'].ewm(span=9,  adjust=False).mean()
        ema20 = df['Close'].ewm(span=20, adjust=False).mean()

        # Get ticker info for additional context
        try:
            info = ticker.info
            company_name = info.get('shortName', symbol)
            prev_close   = round(float(info.get('previousClose', 0) or 0), 2)
        except:
            company_name = symbol
            prev_close   = 0

        candles = []
        for i, (ts, row) in enumerate(df.iterrows()):
            try:
                candles.append({
                    't':    int(ts.timestamp() * 1000),
                    'o':    round(float(row['Open']),  2),
                    'h':    round(float(row['High']),  2),
                    'l':    round(float(row['Low']),   2),
                    'c':    round(float(row['Close']), 2),
                    'v':    int(row['Volume']),
                    'vwap': round(float(vwap_series.iloc[i]), 2),
                    'ema9': round(float(ema9.iloc[i]),  2),
                    'ema20':round(float(ema20.iloc[i]), 2),
                })
            except:
                continue

        if not candles:
            return jsonify({'error': 'Could not process candle data'}), 404

        last = candles[-1]
        change     = round(last['c'] - candles[0]['o'], 2)
        change_pct = round((change / candles[0]['o']) * 100, 2) if candles[0]['o'] else 0

        return jsonify({
            'symbol':      symbol,
            'name':        company_name,
            'interval':    interval,
            'candles':     candles,
            'prev_close':  prev_close,
            'last_price':  last['c'],
            'last_vwap':   last['vwap'],
            'last_ema9':   last['ema9'],
            'last_ema20':  last['ema20'],
            'change':      change,
            'change_pct':  change_pct,
            'above_vwap':  last['c'] > last['vwap'],
            'timestamp':   datetime.now().isoformat(),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
