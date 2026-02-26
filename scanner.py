"""
DayEdge Scanner v4.1 - 488 Stock Watchlist
All 10 Improvements:
1. First-15-min volume confirmation
2. VWAP + key MA institutional levels
3. Earnings reaction history tracking
4. Short interest squeeze detector
5. Sector leader weighting
6. Pre-market volume check
7. News sentiment scoring
8. Gap size normalized to ATR
9. Personal trade logging
10. Time-of-day heat map
"""

import os, json, time, math, requests
import yfinance as yf
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False

NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")

# ── PERSISTENT DATA DIRECTORY ─────────────────────────────────────────────────
# Uses /data/ when running on Railway with a Volume mounted at /data
# Falls back to local directory for development
DATA_DIR = os.environ.get("DATA_DIR", "/data" if os.path.exists("/data") else ".")
os.makedirs(DATA_DIR, exist_ok=True)

def data_path(filename):
    return os.path.join(DATA_DIR, filename)

RESULTS_FILE          = data_path("scan_results.json")
MORNING_FILE          = data_path("morning_golist.json")
HISTORY_FILE          = data_path("scan_history.json")
BACKTEST_FILE         = data_path("backtest_results.json")
GAP_HISTORY_FILE      = data_path("gap_history.json")
ML_MODEL_FILE         = data_path("ml_weights.json")
TIME_OPT_FILE         = data_path("time_optimization.json")
EARNINGS_HISTORY_FILE = data_path("earnings_history.json")
TRADE_LOG_FILE        = data_path("trade_log.json")

DEFAULT_WATCHLIST = [
    # --- Mega Cap Tech ---
    "AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "META", "AMZN", "TSLA",

    # --- Semiconductors ---
    "AVGO", "MU", "SMCI", "ARM", "MRVL", "INTC", "QCOM", "AMAT",
    "LRCX", "KLAC", "MPWR", "ONTO", "CRUS", "SWKS", "WOLF", "AMBA",
    "NXPI", "ADI", "MCHP", "ON", "STX", "WDC", "ASML", "TSM",
    "NVMI", "ACLS", "FORM", "UCTT", "MKSI", "CAMT", "RMBS", "SITM",

    # --- Software / Cloud ---
    "PLTR", "CRM", "SNOW", "DDOG", "NET", "CRWD", "NOW", "ZS",
    "OKTA", "TWLO", "MDB", "BILL", "PATH", "AI", "GTLB", "S",
    "HUBS", "TEAM", "CFLT", "ESTC", "RPD", "TENB", "FORG", "VEEV",
    "DOCU", "DOCN", "APPN", "MNDY", "WIX", "SMAR", "PCTY", "PAYC",
    "COUP", "AZPN", "ANGI", "BOX", "SPSC",
    "ADBE", "ORCL", "INTU", "WDAY", "ADSK", "ANSS", "CDNS", "PTC",
    "MANH", "PEGA", "NCNO", "FRSH", "BRZE", "JAMF", "TOST", "EVCM",

    # --- Cybersecurity ---
    "FTNT", "PANW", "CYBR", "CHKP", "AKAM", "GLOB", "EPAM",

    # --- AI / Quantum Computing ---
    "IONQ", "QUBT", "RGTI", "QBTS", "SOUN", "BBAI", "ARQQ",
    "LAES", "AIXI", "GFAI", "PRCT", "GENI",

    # --- Fintech / Payments ---
    "COIN", "HOOD", "PYPL", "AFRM", "UPST", "SOFI", "MQ", "FOUR",
    "GPN", "FIS", "FLYW", "LC", "OPEN", "RELY", "DAVE", "PAYO",
    "V", "MA", "AXP", "SQ", "FISV", "WU", "DLO", "STNE", "XP",
    "RPAY", "UWMC", "COOP", "NUVEI",

    # --- Consumer Tech / Social ---
    "SHOP", "SPOT", "RBLX", "SNAP", "PINS", "UBER", "LYFT", "ABNB",
    "DASH", "ZM", "ROKU", "NFLX", "BMBL", "MTCH", "DUOL", "APP",
    "DKNG", "PENN", "CPNG", "CART", "MELI", "SE", "GRAB",

    # --- EV / Clean Energy ---
    "RIVN", "LCID", "NIO", "XPEV", "LI", "CHPT", "PLUG", "ENPH",
    "SEDG", "ARRY", "NOVA", "RUN", "BLNK", "EVGO", "BE", "FCEL",
    "SPWR", "STEM", "NKLA", "FSLR", "MAXN", "CSIQ", "DAQO", "SHLS",
    "FLUX", "PTRA",

    # --- Crypto Related ---
    "MSTR", "MARA", "RIOT", "CLSK", "CIFR", "HUT", "WULF", "IREN",
    "BTBT", "CORZ",

    # --- China ADRs ---
    "BABA", "JD", "PDD", "BIDU", "FUTU", "TIGR", "NTES", "EDU", "TAL",
    "NIU", "VNET", "TUYA",

    # --- Biotech / Pharma (Speculative) ---
    "MRNA", "BNTX", "CRSP", "BEAM", "PACB", "ILMN", "HIMS", "TDOC",
    "NVAX", "ALNY", "IONS", "FATE", "EDIT", "NTLA", "INCY", "EXAS",
    "NTRA", "ACCD", "GDRX", "BMRN", "SGEN", "RXRX", "VERV", "TWST",
    "ALLO", "RCKT", "IMVT", "KYMR", "ARWR", "VRTX", "REGN", "BIIB", "GILD",
    "ACAD", "FOLD", "ARDX", "PTGX", "PRAX", "NKTR", "IGMS", "KRTX",
    "TARS", "APLS", "DNLI", "ROIV", "IMCR", "KRYS", "RARE", "VKTX",
    "RYTM", "ACLX", "CGEM", "SNDX",

    # --- Biotech / Pharma (Large Cap) ---
    "ABBV", "LLY", "PFE", "JNJ", "BMY", "MRK", "AZN",

    # --- Defense / Space ---
    "BA", "RKLB", "ASTS", "LUNR", "MNTS", "LMT", "RTX", "NOC", "GD", "KTOS",
    "HII", "TDG", "LDOS", "SAIC", "BAH", "CACI", "HEICO", "TDY", "SPR",

    # --- Banks / Large Cap ---
    "JPM", "BAC", "GS", "MS", "SCHW", "IBKR", "C", "WFC", "BLK",

    # --- Banks / Regional ---
    "USB", "PNC", "TFC", "COF", "KEY", "RF", "ZION", "CFG", "HBAN", "MTB",

    # --- Retail / Consumer ---
    "WMT", "TGT", "COST", "ETSY", "W", "CHWY", "CVNA", "KSS",
    "M", "JWN", "GPS", "ONON", "BIRK", "ELF", "CELH", "LULU", "NKE", "UAA",
    "HD", "LOW", "BBY", "DKS", "FIVE", "OLLI", "BJ", "SFM", "ULTA",
    "BOOT", "CROX", "DECK", "SKX", "WING", "CAVA",

    # --- Restaurants / Food ---
    "MCD", "SBUX", "CMG", "YUM", "QSR", "WEN", "JACK", "TXRH",
    "DINE", "EAT", "DRI", "BLMN", "SHAK", "CAKE", "PLAY",

    # --- Healthcare Devices ---
    "TMO", "DHR", "MDT", "BSX", "EW", "ISRG", "DXCM", "PODD",
    "IRTC", "NVCR", "SWAV", "INMD", "SILK", "MMSI", "LMAT", "ATRC",

    # --- Healthcare Insurance ---
    "UNH", "CVS", "CI", "HUM", "CNC", "MOH", "DOCS", "WELL",

    # --- Energy (Oil & Gas) ---
    "XOM", "CVX", "OXY", "DVN", "HAL", "SLB", "MRO", "FANG", "COP", "BKR",

    # --- Energy (Utilities) ---
    "NEE", "AES", "ETR", "EXC", "PCG", "SRE", "SO",

    # --- Commodities / Mining ---
    "FCX", "NEM", "GOLD", "AG", "WPM", "PAAS", "EXK", "HL",
    "AA", "CLF", "X", "NUE", "STLD", "RS",

    # --- Airlines / Travel ---
    "DAL", "UAL", "AAL", "LUV", "EXPE", "BKNG", "MAR",

    # --- Media / Streaming ---
    "DIS", "WBD", "PARA", "FUBO",

    # --- Industrials ---
    "CAT", "DE", "EMR", "HON", "MMM", "GE", "ETN", "ROK", "ITW",
    "CARR", "OTIS", "XYL", "ROP", "VRSK", "CPRT", "GNRC", "ODFL", "SAIA",

    # --- Meme / High Volatility ---
    "GME", "AMC", "SPCE", "CLOV", "WKHS",
    "TLRY", "SNDL", "ACB", "CRON",

    # --- ETF Proxies (leveraged / sector) ---
    "ARKK", "SOXL", "TQQQ", "UVXY", "LABD",
    "SQQQ", "TNA", "TZA", "SPXU", "UPRO", "TECL", "TECS", "FNGU", "FNGD",

    # --- REITs / Infrastructure ---
    "AMT", "CCI", "EQIX", "DLR", "VICI",
    "O", "PLD", "SPG", "EQR", "AVB", "ESS", "MAA", "OHI", "SBRA",

    # --- Legacy Tech / Enterprise ---
    "IBM", "SAP", "ACN", "TXN", "HPQ", "DELL", "WEX", "GDDY",

    # --- Misc High Growth ---
    "ZI", "TTD", "MGNI", "PUBM", "LMND", "ROOT", "JOBY", "ACHR",
]

# Remove duplicates while preserving order
seen = set()
DEFAULT_WATCHLIST = [x for x in DEFAULT_WATCHLIST if not (x in seen or seen.add(x))]


SECTOR_ETFS = {
    "XLK": ["AAPL","MSFT","NVDA","AMD","GOOGL","META","CRM","TWLO","DDOG","NET","CRWD","OKTA","SNOW","SHOP","INTC","QCOM","AVGO","NOW","ZS","PLTR","AI","ARM","SMCI","AMAT","HUBS","TEAM","MDB","BILL","DOCU","PAYC","ADBE","ORCL","INTU","WDAY","PANW","FTNT","CYBR"],
    "XLF": ["COIN","HOOD","PYPL","AFRM","UPST","SOFI","MQ","FOUR","GPN","FIS","JPM","BAC","GS","MS","SCHW","IBKR","C","WFC","BLK","V","MA","AXP","SQ","FISV","USB","PNC","TFC","COF"],
    "XLY": ["AMZN","TSLA","ABNB","DASH","UBER","LYFT","RBLX","SHOP","ETSY","W","CHWY","CVNA","BMBL","MTCH","DKNG","PENN","MELI","SE","ONON","CELH","LULU","NKE","HD","LOW","MCD","SBUX","CMG","CAVA","WING"],
    "XLC": ["GOOGL","META","NFLX","SNAP","SPOT","ROKU","ZM","PINS","DIS","WBD","PARA","DUOL","APP"],
    "XME": ["MARA","RIOT","CLSK","CIFR","HUT","WULF","IREN","BTBT","CORZ","FCX","NEM","GOLD","AG","WPM","PAAS","AA","CLF","NUE","STLD"],
    "XBI": ["MRNA","BNTX","CRSP","BEAM","NVAX","ALNY","IONS","HIMS","TDOC","ILMN","PACB","INCY","EXAS","NTRA","GDRX","BMRN","SGEN","RXRX","VRTX","REGN","BIIB","GILD","ABBV","LLY","PFE","MRK","ACAD","APLS","KRTX","VKTX"],
    "XLI": ["RIVN","LCID","NIO","XPEV","LI","CHPT","PLUG","ENPH","BLNK","EVGO","BE","RKLB","ASTS","LUNR","BA","LMT","RTX","NOC","HAL","SLB","JOBY","ACHR","CAT","DE","GE","HON","ODFL","SAIA"],
    "XLE": ["XOM","CVX","OXY","DVN","SLB","HAL","MRO","FANG","COP","BKR","NEE","FSLR","ENPH"],
}

# ─── UTILITY ──────────────────────────────────────────────────────────────────

def safe_fetch(symbol, period="30d", interval="1d", prepost=False, retries=2):
    for attempt in range(retries):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval, prepost=prepost)
            return ticker, df
        except Exception as e:
            print(f"  Retry {attempt+1} for {symbol}: {e}")
            time.sleep(1.5)
    return None, None

def load_json(fp, default=None):
    try:
        if os.path.exists(fp):
            with open(fp) as f:
                return json.load(f)
    except: pass
    return default if default is not None else {}

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

def save_json(fp, data):
    try:
        with open(fp, "w") as f:
            json.dump(make_serializable(data), f, indent=2)
    except Exception as e:
        print(f"Error saving {fp}: {e}")

# ─── IMPROVEMENT 1: FIRST-15-MIN VOLUME CONFIRMATION ─────────────────────────

def get_first_15min_rvol(ticker_obj):
    try:
        df = ticker_obj.history(period="30d", interval="15m", prepost=False)
        if df is None or len(df) < 10: return 1.0, 0
        today = datetime.now().date()
        today_bars = df[df.index.date == today]
        if today_bars.empty: return 1.0, 0
        first_bar_vol = int(today_bars.iloc[0]['Volume'])
        hist_first_vols = []
        for date in df.index.date:
            if date == today: continue
            day_bars = df[df.index.date == date]
            if not day_bars.empty:
                hist_first_vols.append(day_bars.iloc[0]['Volume'])
        if not hist_first_vols: return 1.0, first_bar_vol
        avg = sum(hist_first_vols) / len(hist_first_vols)
        if avg == 0: return 1.0, first_bar_vol
        return round(float(first_bar_vol / avg), 2), first_bar_vol
    except: return 1.0, 0

# ─── IMPROVEMENT 2: VWAP + KEY MA INSTITUTIONAL LEVELS ───────────────────────

def get_institutional_levels(df):
    try:
        if len(df) < 5: return {}, 0
        last = float(df['Close'].iloc[-1])
        result = {}
        ma20 = float(df['Close'].tail(20).mean()) if len(df) >= 20 else None
        ma50 = float(df['Close'].tail(50).mean()) if len(df) >= 50 else None
        ma200 = float(df['Close'].tail(200).mean()) if len(df) >= 200 else None
        try:
            typical = (df['High'] + df['Low'] + df['Close']) / 3
            vwap = float((typical * df['Volume']).sum() / df['Volume'].sum())
            result['vwap'] = round(vwap, 2)
            result['above_vwap'] = bool(last > vwap)
        except: result['above_vwap'] = False
        result['ma20'] = round(ma20, 2) if ma20 else None
        result['ma50'] = round(ma50, 2) if ma50 else None
        result['ma200'] = round(ma200, 2) if ma200 else None
        result['above_ma20'] = bool(last > ma20) if ma20 else False
        result['above_ma50'] = bool(last > ma50) if ma50 else False
        result['above_ma200'] = bool(last > ma200) if ma200 else False
        score = sum([result['above_vwap'], result['above_ma20'], result['above_ma50'], result['above_ma200']])
        return result, int(score)
    except: return {}, 0

# ─── IMPROVEMENT 3: EARNINGS REACTION HISTORY ────────────────────────────────

def get_earnings_reaction_history(symbol, ticker_obj):
    history = load_json(EARNINGS_HISTORY_FILE, {})
    if symbol in history and len(history[symbol].get("reactions", [])) >= 3:
        reactions = history[symbol]["reactions"]
        avg_move = sum(abs(r) for r in reactions) / len(reactions)
        avg_direction = sum(reactions) / len(reactions)
        bullish_pct = sum(1 for r in reactions if r > 0) / len(reactions)
        return {
            "avg_move_pct": round(avg_move, 1),
            "avg_direction": round(avg_direction, 1),
            "bullish_pct": round(bullish_pct * 100),
            "samples": len(reactions),
            "is_reliable_gapper": bool(avg_move > 5 and bullish_pct > 0.6)
        }
    return {"avg_move_pct": 0, "bullish_pct": 50, "samples": 0, "is_reliable_gapper": False}

def update_earnings_history(symbol, reaction_pct):
    history = load_json(EARNINGS_HISTORY_FILE, {})
    if symbol not in history:
        history[symbol] = {"reactions": [], "updated": datetime.now().isoformat()}
    history[symbol]["reactions"].append(round(reaction_pct, 2))
    history[symbol]["reactions"] = history[symbol]["reactions"][-12:]
    history[symbol]["updated"] = datetime.now().isoformat()
    save_json(EARNINGS_HISTORY_FILE, history)

# ─── IMPROVEMENT 4: SHORT INTEREST SQUEEZE DETECTOR ──────────────────────────

def get_short_squeeze_score(ticker_obj):
    try:
        info = ticker_obj.info
        short_float = info.get('shortPercentOfFloat', 0) or 0
        short_ratio = info.get('shortRatio', 0) or 0
        if isinstance(short_float, float) and short_float < 1:
            short_float = short_float * 100
        squeeze_score = 0
        if short_float > 20: squeeze_score += 2
        elif short_float > 10: squeeze_score += 1
        if short_ratio > 5: squeeze_score += 1
        return int(squeeze_score), round(float(short_float), 1), round(float(short_ratio), 1)
    except: return 0, 0.0, 0.0

# ─── IMPROVEMENT 5: SECTOR LEADER WEIGHTING ──────────────────────────────────

def get_sector_leader_score(symbol, rotation, df, spy_df):
    try:
        for etf, stocks in SECTOR_ETFS.items():
            if symbol not in stocks: continue
            momentum = rotation.get(etf, {}).get("momentum", "neutral")
            if momentum != "hot": return 0, False
            if spy_df is None or len(df) < 5: return 0, False
            sym_5d = ((df['Close'].iloc[-1] - df['Close'].iloc[-5]) / df['Close'].iloc[-5]) * 100
            spy_5d = ((spy_df['Close'].iloc[-1] - spy_df['Close'].iloc[-5]) / spy_df['Close'].iloc[-5]) * 100
            if sym_5d > spy_5d * 1.5:
                return 2, True
            elif sym_5d > spy_5d:
                return 1, False
        return 0, False
    except: return 0, False

# ─── IMPROVEMENT 6: PRE-MARKET VOLUME ────────────────────────────────────────

def get_premarket_volume(ticker_obj):
    try:
        df = ticker_obj.history(period="5d", interval="1h", prepost=True)
        if df is None or len(df) < 2: return 0, 0.0
        today = datetime.now().date()
        pm_bars = df[(df.index.date == today) & (df.index.hour < 9)]
        if pm_bars.empty: return 0, 0.0
        pm_vol = int(pm_bars['Volume'].sum())
        info_vol = ticker_obj.info.get('averageVolume', 0) or 0
        if info_vol == 0: return pm_vol, 0.0
        pm_vol_pct = round((pm_vol / info_vol) * 100, 1)
        return pm_vol, pm_vol_pct
    except: return 0, 0.0

def get_premarket_change(ticker_obj):
    try:
        df = ticker_obj.history(period="2d", prepost=True, interval="1h")
        if df is None or len(df) < 2: return 0
        today = datetime.now().date()
        pm = df[(df.index.date == today) & (df.index.hour < 9)]
        if pm.empty: return 0
        prev = df[df.index.date < today]['Close']
        if prev.empty: return 0
        return round(((pm['Close'].iloc[-1] - prev.iloc[-1]) / prev.iloc[-1]) * 100, 2)
    except: return 0

# ─── IMPROVEMENT 7: NEWS SENTIMENT SCORING ───────────────────────────────────

BULLISH_KEYWORDS = [
    "beat", "beats", "exceeded", "raised", "upgrade", "upgraded", "buy",
    "outperform", "partnership", "deal", "contract", "launch", "record",
    "growth", "positive", "strong", "surge", "rally", "breakthrough",
    "fda approved", "approval", "wins", "awarded", "expansion"
]
BEARISH_KEYWORDS = [
    "miss", "missed", "below", "lowered", "downgrade", "downgraded", "sell",
    "underperform", "lawsuit", "investigation", "probe", "recall", "cut",
    "loss", "weak", "decline", "warning", "guidance cut", "disappoints",
    "layoffs", "restructuring", "debt", "bankruptcy", "fraud"
]

def get_news_sentiment(symbol):
    if not NEWS_API_KEY: return False, 0, []
    try:
        r = requests.get(
            f"https://newsapi.org/v2/everything?q={symbol}&sortBy=publishedAt&pageSize=5&apiKey={NEWS_API_KEY}",
            timeout=5
        )
        articles = r.json().get("articles", [])
        today = datetime.now().strftime("%Y-%m-%d")
        today_articles = [a for a in articles if a.get("publishedAt", "").startswith(today)]
        if not today_articles: return False, 0, []
        sentiment_score = 0
        headlines = []
        for a in today_articles:
            text = (a.get("title", "") + " " + a.get("description", "")).lower()
            headlines.append(a.get("title", ""))
            bull = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
            bear = sum(1 for kw in BEARISH_KEYWORDS if kw in text)
            sentiment_score += bull - bear
        return True, int(sentiment_score), headlines[:3]
    except: return False, 0, []

# ─── IMPROVEMENT 8: GAP NORMALIZED TO ATR ────────────────────────────────────

def get_gap_atr_ratio(df):
    try:
        gap = calculate_gap_percent(df)
        atr_pct = calculate_atr_percent(df)
        if atr_pct == 0: return 0.0
        return round(float(gap / atr_pct), 2)
    except: return 0.0

# ─── IMPROVEMENT 9: PERSONAL TRADE LOGGING ───────────────────────────────────

def log_trade(symbol, action, entry_price, notes=""):
    trades = load_json(TRADE_LOG_FILE, [])
    trade = {
        "id": f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "symbol": symbol, "action": action, "price": entry_price,
        "notes": notes, "timestamp": datetime.now().isoformat(),
        "exit_price": None, "pnl_pct": None, "outcome": None
    }
    trades.append(trade)
    trades = trades[-500:]
    save_json(TRADE_LOG_FILE, trades)
    return trade

def update_trade_outcome(trade_id, exit_price):
    trades = load_json(TRADE_LOG_FILE, [])
    for t in trades:
        if t["id"] == trade_id and t["exit_price"] is None:
            t["exit_price"] = exit_price
            if t["price"] and exit_price:
                pnl = ((exit_price - t["price"]) / t["price"]) * 100
                t["pnl_pct"] = round(pnl, 2)
                t["outcome"] = "win" if pnl > 0.5 else "loss"
    save_json(TRADE_LOG_FILE, trades)

def get_personal_stats():
    trades = load_json(TRADE_LOG_FILE, [])
    closed = [t for t in trades if t.get("outcome")]
    if not closed: return None
    wins = [t for t in closed if t["outcome"] == "win"]
    losses = [t for t in closed if t["outcome"] == "loss"]
    return {
        "total_trades": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "avg_win_pct": round(sum(t["pnl_pct"] for t in wins) / max(len(wins), 1), 2),
        "avg_loss_pct": round(sum(t["pnl_pct"] for t in losses) / max(len(losses), 1), 2),
        "best_trade": max((t["pnl_pct"] for t in closed), default=0),
        "worst_trade": min((t["pnl_pct"] for t in closed), default=0),
    }

# ─── IMPROVEMENT 10: TIME OF DAY HEAT MAP ────────────────────────────────────

def update_time_heatmap(symbol, entry_hour, exit_hour, pnl_pct):
    opt = load_json(TIME_OPT_FILE, {})
    key = f"hour_{entry_hour}"
    if key not in opt:
        opt[key] = {"wins": 0, "total": 0, "avg_pnl": 0, "pnl_sum": 0}
    opt[key]["total"] += 1
    opt[key]["pnl_sum"] += pnl_pct
    opt[key]["avg_pnl"] = round(opt[key]["pnl_sum"] / opt[key]["total"], 2)
    if pnl_pct > 0.5: opt[key]["wins"] += 1
    save_json(TIME_OPT_FILE, opt)

def get_best_trading_window():
    opt = load_json(TIME_OPT_FILE, {})
    if not opt: return "9:30-10:30 AM (collecting data)"
    best_key = max(opt, key=lambda k: opt[k].get("avg_pnl", 0) if opt[k].get("total", 0) >= 3 else -99)
    bh = int(best_key.replace("hour_", "")); eh = bh + 1
    p = "AM" if bh < 12 else "PM"
    h12 = bh if bh <= 12 else bh - 12; e12 = eh if eh <= 12 else eh - 12
    total = opt[best_key].get("total", 0)
    wr = round(opt[best_key].get("wins", 0) / max(total, 1) * 100)
    return f"{h12}:00-{e12}:00 {p} ({wr}% win rate, {total} trades)"

# ─── CORE FUNCTIONS ───────────────────────────────────────────────────────────

def get_spy_condition():
    try:
        _, df = safe_fetch("SPY", period="60d")
        if df is None or len(df) < 21: return "neutral", 0
        last = df['Close'].iloc[-1]; prev = df['Close'].iloc[-2]
        ma20 = df['Close'].tail(20).mean()
        ma50 = df['Close'].tail(50).mean() if len(df) >= 50 else ma20
        chg = ((last - prev) / prev) * 100
        if chg > 0.5 and last > ma20 and last > ma50: return "bullish", 2
        elif chg > 0.2 and last > ma20: return "bullish", 1
        elif chg < -0.5 or (last < ma20 and last < ma50): return "bearish", -2
        elif chg < -0.2 or last < ma20: return "bearish", -1
        return "neutral", 0
    except: return "neutral", 0

def get_sector_rotation():
    perf = {}
    for etf in SECTOR_ETFS:
        try:
            _, df = safe_fetch(etf, period="30d")
            if df is None or len(df) < 20: continue
            p5 = ((df['Close'].iloc[-1] - df['Close'].iloc[-5]) / df['Close'].iloc[-5]) * 100
            p20 = ((df['Close'].iloc[-1] - df['Close'].iloc[-20]) / df['Close'].iloc[-20]) * 100
            perf[etf] = {
                "perf_5d": round(p5, 2), "perf_20d": round(p20, 2),
                "momentum": "hot" if p5 > 2 else "cold" if p5 < -2 else "neutral"
            }
            time.sleep(0.3)
        except: continue
    return perf

def get_sector_score(symbol, rotation):
    for etf, stocks in SECTOR_ETFS.items():
        if symbol in stocks:
            m = rotation.get(etf, {}).get("momentum", "neutral")
            return (1, etf) if m == "hot" else (-1, etf) if m == "cold" else (0, etf)
    return 0, "Unknown"

def calculate_adx(df, period=14):
    try:
        if len(df) < period + 1: return 0
        h, l, c = df['High'], df['Low'], df['Close']
        pdm = h.diff(); mdm = l.diff().abs()
        pdm[pdm < 0] = 0; mdm[mdm < 0] = 0
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        pdi = 100 * (pdm.rolling(period).mean() / atr)
        mdi = 100 * (mdm.rolling(period).mean() / atr)
        dx = 100 * ((pdi - mdi).abs() / (pdi + mdi))
        adx = dx.rolling(period).mean()
        v = adx.iloc[-1]
        return round(float(v), 1) if not pd.isna(v) else 0
    except: return 0

def check_weekly_trend(symbol):
    try:
        _, df = safe_fetch(symbol, period="1y", interval="1wk")
        if df is None or len(df) < 10: return 0
        c = df['Close']; ma = c.tail(10).mean(); last = c.iloc[-1]
        wchg = ((last - c.iloc[-2]) / c.iloc[-2]) * 100
        if last > ma and wchg > 0: return 1
        elif last < ma and wchg < 0: return -1
        return 0
    except: return 0

def calculate_gap_percent(df):
    try:
        if len(df) < 2: return 0
        return round(((df['Open'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100, 2)
    except: return 0

def calculate_relative_volume(df):
    try:
        if len(df) < 6: return 1.0
        avg = df['Volume'].iloc[-6:-1].mean()
        if avg == 0: return 1.0
        return round(float(df['Volume'].iloc[-1] / avg), 2)
    except: return 1.0

def calculate_atr(df, period=7):
    try:
        r = df.tail(period)
        return float((r['High'] - r['Low']).mean())
    except: return 0

def calculate_atr_percent(df):
    try:
        atr = calculate_atr(df); last = df['Close'].iloc[-1]
        return round((atr / last) * 100, 2)
    except: return 0

def calculate_dollar_volume(df):
    try: return float(df['Close'].iloc[-1] * df['Volume'].iloc[-1])
    except: return 0

def check_clean_technical_level(df):
    score = 0
    try:
        c = df['Close']; last = c.iloc[-1]
        if last >= df['High'].tail(10).max() * 0.98: score += 1
        if len(c) >= 5 and last > c.tail(5).mean(): score += 1
        dh, dl = df['High'].iloc[-1], df['Low'].iloc[-1]; dr = dh - dl
        if dr > 0 and (last - dl) / dr >= 0.75: score += 1
    except: pass
    return score

def get_relative_strength(sdf, spy_df):
    try:
        if len(sdf) < 5 or spy_df is None or len(spy_df) < 5: return 0
        sc = ((sdf['Close'].iloc[-1] - sdf['Close'].iloc[-5]) / sdf['Close'].iloc[-5]) * 100
        sp = ((spy_df['Close'].iloc[-1] - spy_df['Close'].iloc[-5]) / spy_df['Close'].iloc[-5]) * 100
        rs = sc - sp
        if rs > 3: return 2
        elif rs > 1: return 1
        elif rs < -3: return -2
        elif rs < -1: return -1
        return 0
    except: return 0

def get_float_score(ticker_obj):
    try:
        fs = ticker_obj.info.get('floatShares')
        if fs is None: return 0, None
        fm = fs / 1_000_000
        if fm < 20: return 2, round(fm, 1)
        elif fm < 50: return 1, round(fm, 1)
        elif fm > 500: return -1, round(fm, 1)
        return 0, round(fm, 1)
    except: return 0, None

def get_earnings_risk(ticker_obj):
    try:
        cal = ticker_obj.calendar
        if cal is None: return False, None
        if isinstance(cal, dict):
            ed = cal.get('Earnings Date')
            if ed is None: return False, None
            if hasattr(ed, '__iter__') and not isinstance(ed, str): ed = list(ed)[0]
        elif hasattr(cal, 'loc'):
            try: ed = cal.loc['Earnings Date'].iloc[0]
            except: return False, None
        else: return False, None
        if hasattr(ed, 'date'): ed = ed.date()
        days = (ed - datetime.now().date()).days
        return (True, days) if 0 <= days <= 3 else (False, days)
    except: return False, None

def get_risk_reward(df):
    try:
        last = df['Close'].iloc[-1]
        td = df['High'].tail(10).max() - last
        sd = last - df['Low'].tail(10).min()
        if sd <= 0: return None
        return round(float(td / sd), 2)
    except: return None

def calculate_trade_levels(df):
    try:
        last = float(df['Close'].iloc[-1]); atr = calculate_atr(df)
        entry = round(last * 1.002, 2)
        stop = round(max(entry - (1.5 * atr), float(df['Low'].tail(5).min()) * 0.99), 2)
        risk = entry - stop
        return {
            "entry": entry, "stop": stop,
            "stop_pct": round(((entry - stop) / entry) * 100, 2),
            "target1": round(entry + risk, 2),
            "target2": round(entry + 2 * risk, 2),
            "target3": round(entry + 3 * risk, 2),
            "resistance": round(float(df['High'].tail(10).max()), 2),
            "atr": round(atr, 2)
        }
    except: return None

def get_gap_fill_risk(symbol, gap_pct):
    h = load_json(GAP_HISTORY_FILE, {}).get(symbol, {})
    total = h.get("total", 0)
    if total < 5: return None, 0
    fr = h["filled"] / total; cr = h.get("continued", 0) / total
    if cr > 0.7 and gap_pct > 0: return fr, 1
    elif fr > 0.7 and gap_pct > 0: return fr, -1
    return fr, 0

def check_unusual_options(symbol):
    try:
        t = yf.Ticker(symbol); dates = t.options
        if not dates: return False, None
        chain = t.option_chain(dates[0])
        calls, puts = chain.calls, chain.puts
        if calls.empty or puts.empty: return False, None
        cv = calls['volume'].sum(); pv = puts['volume'].sum()
        coi = calls['openInterest'].sum(); poi = puts['openInterest'].sum()
        if coi == 0: return False, None
        voi = (cv + pv) / (coi + poi) if (coi + poi) > 0 else 0
        unusual = bool(voi > 0.5 and cv > pv * 1.5)
        return unusual, {
            "put_call_ratio": round(float(pv / cv) if cv > 0 else 0, 2),
            "vol_oi_ratio": round(float(voi), 2),
            "call_volume": int(cv), "put_volume": int(pv)
        }
    except: return False, None

def train_ml_model():
    if not ML_AVAILABLE: return None
    history = load_json(HISTORY_FILE, [])
    picks = [p for e in history for p in e.get("picks", []) if p.get("outcome") and p.get("features")]
    if len(picks) < 20:
        print(f"Not enough data for ML ({len(picks)} picks, need 20+)")
        return None
    try:
        keys = ["gap_pct","rvol","atr_pct","tech_score","adx","weekly_trend",
                "float_score","rs_score","pm_change","spy_modifier","sector_score",
                "rr_ratio","short_squeeze_score","gap_atr_ratio","institutional_score"]
        X = [[p["features"].get(k, 0) or 0 for k in keys] for p in picks]
        y = [1 if p["outcome"] == "win" else 0 for p in picks]
        sc = StandardScaler(); Xs = sc.fit_transform(X)
        m = LogisticRegression(max_iter=1000, random_state=42).fit(Xs, y)
        data = {
            "weights": dict(zip(keys, m.coef_[0].tolist())),
            "trained_on": len(picks),
            "timestamp": datetime.now().isoformat(),
            "scaler_mean": sc.mean_.tolist(),
            "scaler_scale": sc.scale_.tolist(),
            "feature_keys": keys
        }
        save_json(ML_MODEL_FILE, data)
        print(f"ML trained on {len(picks)} picks")
        return data
    except Exception as e:
        print(f"ML error: {e}"); return None

def get_ml_adjustment(features):
    ml = load_json(ML_MODEL_FILE, None)
    if not ml or not ML_AVAILABLE: return 0
    try:
        keys = ml["feature_keys"]; w = ml["weights"]
        mean = np.array(ml["scaler_mean"]); scale = np.array(ml["scaler_scale"])
        row = np.array([features.get(k, 0) or 0 for k in keys])
        scaled = (row - mean) / scale
        logit = sum(scaled[i] * w[keys[i]] for i in range(len(keys)))
        prob = 1 / (1 + math.exp(-logit))
        return round((prob - 0.5) * 2, 2)
    except: return 0

def run_backtest(symbols=None, days=60):
    print("Running backtest engine...")
    if symbols is None: symbols = DEFAULT_WATCHLIST[:10]
    res = {
        "run_date": datetime.now().isoformat(), "total_signals": 0,
        "winning_signals": 0, "win_rate": 0, "avg_gain": 0, "avg_loss": 0,
        "by_grade": {"A": {"wins": 0, "total": 0}, "B": {"wins": 0, "total": 0}, "C": {"wins": 0, "total": 0}}
    }
    gains, losses = [], []
    _, spy_full = safe_fetch("SPY", period="6mo")
    for sym in symbols:
        print(f"  Backtesting {sym}...")
        time.sleep(0.5)
        _, df = safe_fetch(sym, period="6mo")
        if df is None or len(df) < 30: continue
        for i in range(20, min(len(df) - 1, days)):
            try:
                sl = df.iloc[:i + 1]; last = float(sl['Close'].iloc[-1])
                dv = calculate_dollar_volume(sl)
                if dv < 10_000_000: continue
                g = calculate_gap_percent(sl); r = calculate_relative_volume(sl)
                t = check_clean_technical_level(sl); adx = calculate_adx(sl)
                score = 0
                if 2 <= g <= 8: score += 3
                elif 0.5 <= g < 2: score += 1
                if r >= 2: score += 2
                elif r >= 1.5: score += 1
                score += t
                if adx > 25: score += 1
                if last < 5: score -= 2
                n = round(min(10, max(0, (score / 12) * 10)), 1)
                grade = "A" if n >= 8 else "B" if n >= 6 else "C"
                if n < 3: continue
                nxt = float(df['Close'].iloc[i + 1]); pct = ((nxt - last) / last) * 100; won = pct > 1
                res["total_signals"] += 1
                if won: res["winning_signals"] += 1; gains.append(pct)
                else: losses.append(pct)
                res["by_grade"][grade]["total"] += 1
                if won: res["by_grade"][grade]["wins"] += 1
            except: continue
    if res["total_signals"] > 0:
        res["win_rate"] = round((res["winning_signals"] / res["total_signals"]) * 100, 1)
    if gains: res["avg_gain"] = round(sum(gains) / len(gains), 2)
    if losses: res["avg_loss"] = round(sum(losses) / len(losses), 2)
    for g in ["A", "B", "C"]:
        d = res["by_grade"][g]
        d["win_rate"] = round((d["wins"] / d["total"]) * 100, 1) if d["total"] > 0 else 0
    save_json(BACKTEST_FILE, res)
    print(f"Backtest done. Win rate: {res['win_rate']}% on {res['total_signals']} signals")
    return res

def save_scan_to_history(results):
    try:
        h = load_json(HISTORY_FILE, [])
        entry = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "picks": [{
                "symbol": r["symbol"], "score": r["score"], "grade": r["grade"],
                "last_close": r["last_close"], "features": r.get("features", {}),
                "outcome": None, "outcome_pct": None
            } for r in results]
        }
        h = [x for x in h if x["date"] != entry["date"]]
        h.append(entry); h = h[-90:]
        save_json(HISTORY_FILE, h)
    except Exception as e: print(f"History save error: {e}")

def update_outcomes():
    try:
        h = load_json(HISTORY_FILE, [])
        yest = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        for entry in h:
            if entry["date"] == yest:
                for pick in entry["picks"]:
                    if pick["outcome"] is None:
                        try:
                            _, df = safe_fetch(pick["symbol"], period="5d")
                            if df is not None and len(df) >= 1:
                                pct = round(((float(df['Close'].iloc[-1]) - pick["last_close"]) / pick["last_close"]) * 100, 2)
                                pick["outcome"] = "win" if pct > 1 else "loss"
                                pick["outcome_pct"] = pct
                            time.sleep(0.3)
                        except: pass
        save_json(HISTORY_FILE, h)
    except Exception as e: print(f"Outcome update error: {e}")

def calculate_win_rate():
    try:
        h = load_json(HISTORY_FILE, [])
        picks = [p for e in h for p in e.get("picks", []) if p.get("outcome")]
        if not picks: return None
        total = len(picks); wins = sum(1 for p in picks if p["outcome"] == "win")
        gs = {}
        for g in ["A", "B", "C"]:
            gp = [p for p in picks if p["grade"] == g]
            if gp:
                gw = sum(1 for p in gp if p["outcome"] == "win")
                gs[g] = {
                    "win_rate": round((gw / len(gp)) * 100, 1), "total": len(gp),
                    "avg_gain": round(sum(p.get("outcome_pct", 0) for p in gp if p["outcome"] == "win") / max(gw, 1), 2)
                }
        return {"overall_win_rate": round((wins / total) * 100, 1), "total_picks": total, "total_wins": wins, "by_grade": gs}
    except: return None

def run_morning_scan():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Morning confirmation scan...")
    last = load_json(RESULTS_FILE, {}); picks = last.get("results", [])
    if not picks:
        return {"golist": [], "timestamp": datetime.now().isoformat(), "message": "No evening scan results found"}
    golist = []
    # ── SPY market condition check ──────────────────────────────────────────
    spy_condition = "unknown"
    try:
        spy_ticker = yf.Ticker("SPY")
        spy_df = spy_ticker.history(period="1d", interval="5m")
        if spy_df is not None and len(spy_df) >= 5:
            spy_price = float(spy_df['Close'].iloc[-1])
            spy_open  = float(spy_df['Open'].iloc[0])
            spy_chg   = (spy_price - spy_open) / spy_open * 100
            spy_vwap  = float((spy_df[['High','Low','Close']].mean(axis=1) * spy_df['Volume']).cumsum().iloc[-1] / spy_df['Volume'].cumsum().iloc[-1])
            if spy_chg > 0.3 and spy_price > spy_vwap:
                spy_condition = "bullish"
            elif spy_chg < -0.3 and spy_price < spy_vwap:
                spy_condition = "bearish"
            else:
                spy_condition = "neutral"
            print(f"[MORNING] SPY condition: {spy_condition} ({spy_chg:+.2f}%)")
    except Exception as e:
        print(f"[MORNING] SPY check error: {e}")

    for pick in picks:
        sym = pick["symbol"]
        try:
            time.sleep(0.5)
            ticker, df = safe_fetch(sym, period="5d")
            if ticker is None or df is None: continue
            pm = get_premarket_change(ticker)
            pm_vol, pm_vol_pct = get_premarket_volume(ticker)
            first_15_rvol, first_15_vol = get_first_15min_rvol(ticker)

            # ── Filter 1: Minimum PM change ──────────────────────────────────
            if pm <= 0.3:
                continue

            # ── Filter 2: Skip if SPY is bearish (protect longs) ────────────
            if spy_condition == "bearish":
                print(f"  [MORNING] Skipping {sym} — SPY bearish")
                continue

            tl = calculate_trade_levels(df)

            # ── Filter 3: Minimum R/R ratio of 2.0 ──────────────────────────
            rr = tl.get("rr_ratio", 0) if tl else 0
            if rr and float(rr) < 2.0:
                print(f"  [MORNING] Skipping {sym} — R/R {rr} below 2.0")
                continue

            # ── Calculate VWAP for trade reference ───────────────────────────
            vwap = None
            try:
                intra = ticker.history(period="1d", interval="5m")
                if intra is not None and len(intra) > 0:
                    typical = intra[['High','Low','Close']].mean(axis=1)
                    vwap = round(float((typical * intra['Volume']).cumsum().iloc[-1] / intra['Volume'].cumsum().iloc[-1]), 2)
            except:
                pass

            # ── Check for catalyst ───────────────────────────────────────────
            has_catalyst = pick.get("has_catalyst", False)

            golist.append({
                "symbol": sym, "evening_score": pick["score"], "grade": pick["grade"],
                "prev_close": pick["last_close"], "pm_change": pm,
                "pm_volume": pm_vol, "pm_vol_pct": pm_vol_pct,
                "first_15min_rvol": first_15_rvol,
                "has_catalyst": has_catalyst,
                "vwap": vwap,
                "spy_condition": spy_condition,
                "trade_levels": tl, "best_window": get_best_trading_window()
            })
        except Exception as e: print(f"  Morning error {sym}: {e}")

    # Sort: catalyst stocks first, then by grade, then by pm_change
    def morning_sort_key(x):
        grade_order = {"A": 0, "B": 1, "C": 2}
        catalyst_order = 0 if x.get("has_catalyst") else 1
        return (catalyst_order, grade_order.get(x.get("grade","C"), 2), -x.get("pm_change", 0))

    golist.sort(key=morning_sort_key)
    out = {
        "timestamp": datetime.now().isoformat(),
        "golist": golist, "total_confirmed": len(golist),
        "best_window": get_best_trading_window(),
        "personal_stats": get_personal_stats()
    }
    save_json(MORNING_FILE, out)
    print(f"Morning scan done. {len(golist)} confirmed.")
    return out

# ─── MAIN SCANNER ─────────────────────────────────────────────────────────────

def run_scanner():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] DayEdge v4.1 starting — {len(DEFAULT_WATCHLIST)} stocks...")
    update_outcomes()
    train_ml_model()
    print("Checking SPY..."); spy_cond, spy_mod = get_spy_condition()
    print(f"  Market: {spy_cond} ({spy_mod:+d})")
    print("Sector rotation..."); rotation = get_sector_rotation()
    _, spy_df = safe_fetch("SPY", period="30d")
    results = []

    for i, sym in enumerate(DEFAULT_WATCHLIST):
        try:
            print(f"[{i+1}/{len(DEFAULT_WATCHLIST)}] {sym}...")
            time.sleep(0.5)
            ticker, df = safe_fetch(sym, period="60d")
            if ticker is None or df is None or len(df) < 10: continue
            last = float(df['Close'].iloc[-1]); dv = calculate_dollar_volume(df)
            if dv < 5_000_000: continue

            float_score, float_m = get_float_score(ticker)
            earnings_risky, days_earn = get_earnings_risk(ticker)
            earnings_risky = bool(earnings_risky)
            sector_score, sector_etf = get_sector_score(sym, rotation)
            unusual_options, options_detail = check_unusual_options(sym)
            unusual_options = bool(unusual_options)
            gap_fill_prob, gap_fill_mod = get_gap_fill_risk(sym, calculate_gap_percent(df))
            trade_levels = calculate_trade_levels(df)
            inst_levels, institutional_score = get_institutional_levels(df)
            short_squeeze, short_float, short_ratio = get_short_squeeze_score(ticker)
            sector_leader_score, is_sector_leader = get_sector_leader_score(sym, rotation, df, spy_df)
            pm_vol, pm_vol_pct = get_premarket_volume(ticker)
            has_news, sentiment_score, headlines = get_news_sentiment(sym)
            gap_atr_ratio = get_gap_atr_ratio(df)
            earnings_reaction = get_earnings_reaction_history(sym, ticker)

            features = {
                "gap_pct": calculate_gap_percent(df),
                "rvol": calculate_relative_volume(df),
                "atr_pct": calculate_atr_percent(df),
                "tech_score": int(check_clean_technical_level(df)),
                "adx": calculate_adx(df),
                "has_catalyst": bool(has_news),
                "sentiment_score": int(sentiment_score),
                "last_close": last,
                "spy_modifier": int(spy_mod),
                "sector_score": int(sector_score),
                "float_score": int(float_score),
                "earnings_risky": earnings_risky,
                "rs_score": int(get_relative_strength(df, spy_df)),
                "pm_change": get_premarket_change(ticker),
                "pm_vol_pct": pm_vol_pct,
                "rr_ratio": get_risk_reward(df),
                "weekly_trend": int(check_weekly_trend(sym)),
                "dollar_vol": dv,
                "gap_fill_modifier": int(gap_fill_mod),
                "unusual_options": unusual_options,
                "institutional_score": int(institutional_score),
                "short_squeeze_score": int(short_squeeze),
                "sector_leader_score": int(sector_leader_score),
                "gap_atr_ratio": gap_atr_ratio,
                "earnings_is_reliable_gapper": bool(earnings_reaction.get("is_reliable_gapper", False)),
            }

            ml_adj = get_ml_adjustment(features)
            score, reasons = score_stock_v4(features, ml_adj)

            if score >= 3:
                results.append({
                    "symbol": sym, "score": score, "last_close": round(last, 2),
                    "gap_pct": features["gap_pct"], "rvol": features["rvol"],
                    "atr_pct": features["atr_pct"], "adx": features["adx"],
                    "volume": int(df['Volume'].iloc[-1]), "dollar_vol_m": round(dv / 1_000_000, 1),
                    "pm_change": features["pm_change"], "pm_vol_pct": pm_vol_pct,
                    "float_m": float_m, "sector_etf": sector_etf,
                    "earnings_risky": earnings_risky, "days_to_earnings": days_earn,
                    "rs_score": features["rs_score"], "rr_ratio": features["rr_ratio"],
                    "weekly_trend": features["weekly_trend"],
                    "unusual_options": unusual_options, "options_detail": options_detail,
                    "gap_fill_prob": gap_fill_prob, "has_catalyst": features["has_catalyst"],
                    "sentiment_score": sentiment_score, "headlines": headlines,
                    "tech_score": features["tech_score"], "trade_levels": trade_levels,
                    "institutional_levels": inst_levels, "institutional_score": institutional_score,
                    "short_float_pct": short_float, "short_ratio": short_ratio,
                    "short_squeeze_score": short_squeeze,
                    "is_sector_leader": bool(is_sector_leader),
                    "gap_atr_ratio": gap_atr_ratio,
                    "earnings_reaction": earnings_reaction,
                    "ml_adjustment": ml_adj, "reasons": reasons, "features": features,
                    "grade": "A" if score >= 8 else "B" if score >= 6 else "C"
                })
        except Exception as e:
            print(f"  Error {sym}: {e}"); continue

    results.sort(key=lambda x: x['score'], reverse=True); results = results[:15]
    output = {
        "timestamp": datetime.now().isoformat(),
        "market_date": (datetime.now() + timedelta(days=1)).strftime("%A, %B %d %Y"),
        "total_scanned": len(DEFAULT_WATCHLIST), "market_condition": spy_cond,
        "spy_modifier": spy_mod, "sector_rotation": rotation,
        "best_trading_window": get_best_trading_window(),
        "win_rate": calculate_win_rate(), "backtest": load_json(BACKTEST_FILE, None),
        "personal_stats": get_personal_stats(),
        "results": results
    }
    save_json(RESULTS_FILE, output)
    save_scan_to_history(results)
    print(f"Done. {len(results)} setups found from {len(DEFAULT_WATCHLIST)} stocks. Market: {spy_cond}")
    return output

# ─── SCORING ENGINE v4 ────────────────────────────────────────────────────────

def score_stock_v4(features, ml_adj=0):
    score = 0; reasons = []
    g = features.get("gap_pct", 0); rvol = features.get("rvol", 1)
    atr = features.get("atr_pct", 0); tech = features.get("tech_score", 0)
    cat = features.get("has_catalyst", False); last = features.get("last_close", 0)
    spy = features.get("spy_modifier", 0); sec = features.get("sector_score", 0)
    flt = features.get("float_score", 0); earn = features.get("earnings_risky", False)
    rs = features.get("rs_score", 0); pm = features.get("pm_change", 0)
    rr = features.get("rr_ratio"); adx = features.get("adx", 0)
    wt = features.get("weekly_trend", 0); dv = features.get("dollar_vol", 0)
    gfm = features.get("gap_fill_modifier", 0); uo = features.get("unusual_options", False)
    inst = features.get("institutional_score", 0)
    squeeze = features.get("short_squeeze_score", 0)
    leader = features.get("sector_leader_score", 0)
    pm_vol = features.get("pm_vol_pct", 0)
    sentiment = features.get("sentiment_score", 0)
    gap_atr = features.get("gap_atr_ratio", 0)
    reliable_gapper = features.get("earnings_is_reliable_gapper", False)

    if dv < 10_000_000: return 0, ["🔴 Dollar volume under $10M — excluded"]

    if 2 <= g <= 8: score += 3; reasons.append(f"✅ Ideal gap ({g}%)")
    elif 8 < g <= 15: score += 2; reasons.append(f"⚠️ Large gap ({g}%) — may be extended")
    elif 0.5 <= g < 2: score += 1; reasons.append(f"🔹 Small gap ({g}%)")
    elif g < -2: score -= 1; reasons.append(f"🔴 Gapping down ({g}%)")
    if gap_atr >= 3: score += 1; reasons.append(f"✅ Gap is {gap_atr}x ATR — significant move")
    elif gap_atr >= 2: reasons.append(f"🔹 Gap is {gap_atr}x ATR — solid move")

    score += gfm
    if gfm > 0: reasons.append("✅ Historically continues gaps")
    elif gfm < 0: reasons.append("⚠️ Historically fills gaps — caution")

    if pm > 2: score += 2; reasons.append(f"✅ Strong pre-market ({pm}%)")
    elif pm > 0.5: score += 1; reasons.append(f"🔹 Pre-market positive ({pm}%)")
    elif pm < -1: score -= 1; reasons.append(f"⚠️ Pre-market weak ({pm}%)")
    if pm_vol > 20: score += 1; reasons.append(f"✅ Heavy pre-market volume ({pm_vol}% of avg daily)")
    elif pm_vol > 10: reasons.append(f"🔹 Decent pre-market volume ({pm_vol}% of avg daily)")

    if rvol >= 3: score += 3; reasons.append(f"✅ Very high RVOL ({rvol}x)")
    elif rvol >= 2: score += 2; reasons.append(f"✅ High RVOL ({rvol}x)")
    elif rvol >= 1.5: score += 1; reasons.append(f"🔹 Above avg RVOL ({rvol}x)")
    else: reasons.append(f"⚠️ Low RVOL ({rvol}x)")

    score += tech
    if tech == 3: reasons.append("✅ Strong technical setup")
    elif tech == 2: reasons.append("🔹 Decent technical setup")
    elif tech == 1: reasons.append("⚠️ Weak technical setup")

    if inst >= 3: score += 2; reasons.append("✅ Above VWAP + all key MAs — institutional support")
    elif inst == 2: score += 1; reasons.append("🔹 Above most key levels")
    elif inst == 1: reasons.append("⚠️ Mixed signals on key levels")
    elif inst == 0: score -= 1; reasons.append("🔴 Below key institutional levels")

    if adx > 30: score += 2; reasons.append(f"✅ Very strong trend (ADX {adx})")
    elif adx > 25: score += 1; reasons.append(f"✅ Strong trend (ADX {adx})")
    elif adx < 20: score -= 1; reasons.append(f"⚠️ Choppy/weak trend (ADX {adx})")

    if wt == 1: score += 2; reasons.append("✅ Weekly uptrend confirmed")
    elif wt == -1: score -= 2; reasons.append("🔴 Fighting weekly downtrend")

    if squeeze >= 2: score += 2; reasons.append("🔥 High short interest — squeeze potential!")
    elif squeeze == 1: score += 1; reasons.append("⚡ Moderate short interest — some squeeze risk")

    if leader >= 2: score += 2; reasons.append("⭐ Sector leader — strongest in hot sector!")
    elif leader == 1: score += 1; reasons.append("🔹 Outperforming sector peers")

    if sentiment >= 2: score += 2; reasons.append("✅ Strongly bullish news sentiment")
    elif sentiment == 1: score += 1; reasons.append("🔹 Positive news sentiment")
    elif cat and sentiment == 0: score += 1; reasons.append("✅ News catalyst detected")
    elif sentiment <= -2: score -= 2; reasons.append("🔴 Bearish news sentiment")
    elif sentiment == -1: score -= 1; reasons.append("⚠️ Slightly negative news")

    if uo: score += 1; reasons.append("✅ Unusual options activity")

    if earn and reliable_gapper:
        score += 1; reasons.append("✅ Earnings risk BUT historically gaps up big")
    elif earn:
        score -= 3; reasons.append("🔴 EARNINGS WITHIN 3 DAYS — high risk")

    score += spy
    if spy > 0: reasons.append("✅ Market bullish (SPY)")
    elif spy < 0: reasons.append("🔴 Market bearish (SPY) — caution")

    score += sec
    if sec > 0: reasons.append("✅ Sector strong")
    elif sec < 0: reasons.append("⚠️ Sector weak")

    score += flt
    if flt == 2: reasons.append("✅ Low float — big move potential")
    elif flt == 1: reasons.append("🔹 Moderate float")
    elif flt < 0: reasons.append("⚠️ High float")

    score += rs
    if rs > 0: reasons.append("✅ Outperforming SPY")
    elif rs < 0: reasons.append("⚠️ Underperforming SPY")

    if atr < 1.5: score -= 1; reasons.append(f"⚠️ Low volatility ({atr}% ATR)")
    elif atr >= 3: reasons.append(f"✅ Good daily range ({atr}% ATR)")
    if last < 5: score -= 2; reasons.append("🔴 Price under $5")
    elif last > 500: reasons.append("ℹ️ High price — size accordingly")
    if rr is not None:
        if rr >= 2: score += 1; reasons.append(f"✅ Good R/R ({rr}:1)")
        elif rr < 1: score -= 1; reasons.append(f"⚠️ Poor R/R ({rr}:1)")

    if ml_adj != 0:
        pts = round(ml_adj * 2); score += pts
        reasons.append(f"🤖 ML {'boost' if pts > 0 else 'caution'} ({'+' if pts > 0 else ''}{pts}pts)")

    return round(min(10, max(0, (score / 26) * 10)), 1), reasons

# ─── API ENDPOINTS FOR TRADE LOGGING ─────────────────────────────────────────

def api_log_trade(symbol, action, price, notes=""):
    return log_trade(symbol, action, price, notes)

def api_close_trade(trade_id, exit_price):
    update_trade_outcome(trade_id, exit_price)
    trades = load_json(TRADE_LOG_FILE, [])
    for t in trades:
        if t["id"] == trade_id and t.get("pnl_pct") is not None:
            entry_hour = datetime.fromisoformat(t["timestamp"]).hour
            update_time_heatmap(symbol=t["symbol"], entry_hour=entry_hour, exit_hour=datetime.now().hour, pnl_pct=t["pnl_pct"])

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "backtest": run_backtest()
    elif len(sys.argv) > 1 and sys.argv[1] == "morning": run_morning_scan()
    else: run_scanner()
