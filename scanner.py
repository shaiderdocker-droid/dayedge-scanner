"""
Evening Stock Scanner - Day Trading Setup v2.0
Improvements:
- Pre-market data
- Sector momentum filter
- SPY market condition check
- Float size scoring
- Earnings date awareness
- Relative strength vs SPY
- Win rate tracking
- Risk/reward filter
- Rate limiting protection
"""

import os
import json
import time
import requests
import yfinance as yf
from datetime import datetime, timedelta
import pandas as pd

NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
RESULTS_FILE = "scan_results.json"
HISTORY_FILE = "scan_history.json"

DEFAULT_WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "GOOGL", "AMZN",
    "NFLX", "CRM", "PLTR", "SOFI", "RIVN", "LCID", "NIO", "BABA",
    "COIN", "HOOD", "MARA", "RIOT", "UPST", "AFRM", "SQ", "PYPL",
    "SNAP", "UBER", "LYFT", "ABNB", "DASH", "RBLX", "SHOP", "SPOT",
    "ZM", "ROKU", "TWLO", "DDOG", "NET", "CRWD", "OKTA", "SNOW"
]

SECTOR_ETFS = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "META", "CRM", "TWLO", "DDOG", "NET", "CRWD", "OKTA", "SNOW", "SHOP"],
    "XLF": ["COIN", "HOOD", "PYPL", "SQ", "AFRM", "UPST"],
    "XLY": ["AMZN", "TSLA", "ABNB", "DASH", "UBER", "LYFT", "RBLX", "SHOP"],
    "XLC": ["GOOGL", "META", "NFLX", "SNAP", "SPOT", "ROKU", "ZM"],
    "XME": ["MARA", "RIOT"],
    "XBI": ["SOFI"],
    "XLI": ["RIVN", "LCID", "NIO", "BABA"],
}


def safe_fetch(symbol, period="30d", retries=2):
    for attempt in range(retries):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period)
            return ticker, df
        except Exception as e:
            print(f"  Retry {attempt+1} for {symbol}: {e}")
            time.sleep(1.5)
    return None, None


def get_spy_condition():
    try:
        _, df = safe_fetch("SPY", period="30d")
        if df is None or len(df) < 21:
            return "neutral", 0
        last_close = df['Close'].iloc[-1]
        prev_close = df['Close'].iloc[-2]
        ma20 = df['Close'].tail(20).mean()
        day_change = ((last_close - prev_close) / prev_close) * 100
        above_ma = last_close > ma20
        if day_change > 0.5 and above_ma:
            return "bullish", 2
        elif day_change < -0.5 or not above_ma:
            return "bearish", -2
        else:
            return "neutral", 0
    except:
        return "neutral", 0


def get_sector_strength(symbol, sector_cache):
    sector_etf = None
    for etf, stocks in SECTOR_ETFS.items():
        if symbol in stocks:
            sector_etf = etf
            break
    if not sector_etf:
        return 0, "Unknown"
    if sector_etf in sector_cache:
        return sector_cache[sector_etf]
    try:
        _, df = safe_fetch(sector_etf, period="10d")
        if df is None or len(df) < 2:
            result = (0, sector_etf)
        else:
            last_close = df['Close'].iloc[-1]
            prev_close = df['Close'].iloc[-2]
            change = ((last_close - prev_close) / prev_close) * 100
            if change > 0.3:
                result = (1, sector_etf)
            elif change < -0.3:
                result = (-1, sector_etf)
            else:
                result = (0, sector_etf)
        sector_cache[sector_etf] = result
        return result
    except:
        return 0, sector_etf


def get_float_score(ticker_obj):
    try:
        info = ticker_obj.info
        float_shares = info.get('floatShares', None)
        if float_shares is None:
            return 0, None
        float_m = float_shares / 1_000_000
        if float_m < 20:
            return 2, round(float_m, 1)
        elif float_m < 50:
            return 1, round(float_m, 1)
        elif float_m > 500:
            return -1, round(float_m, 1)
        else:
            return 0, round(float_m, 1)
    except:
        return 0, None


def get_earnings_risk(ticker_obj):
    try:
        cal = ticker_obj.calendar
        if cal is None:
            return False, None
        if isinstance(cal, dict):
            earnings_date = cal.get('Earnings Date', None)
            if earnings_date is None:
                return False, None
            if hasattr(earnings_date, '__iter__') and not isinstance(earnings_date, str):
                earnings_date = list(earnings_date)[0]
        elif hasattr(cal, 'loc'):
            try:
                earnings_date = cal.loc['Earnings Date'].iloc[0]
            except:
                return False, None
        else:
            return False, None
        if hasattr(earnings_date, 'date'):
            earnings_date = earnings_date.date()
        today = datetime.now().date()
        days_away = (earnings_date - today).days
        if 0 <= days_away <= 3:
            return True, days_away
        return False, days_away
    except:
        return False, None


def get_relative_strength(stock_df, spy_df):
    try:
        if len(stock_df) < 5 or spy_df is None or len(spy_df) < 5:
            return 0
        stock_change = ((stock_df['Close'].iloc[-1] - stock_df['Close'].iloc[-5]) / stock_df['Close'].iloc[-5]) * 100
        spy_change = ((spy_df['Close'].iloc[-1] - spy_df['Close'].iloc[-5]) / spy_df['Close'].iloc[-5]) * 100
        rs = stock_change - spy_change
        if rs > 3:
            return 2
        elif rs > 1:
            return 1
        elif rs < -3:
            return -2
        elif rs < -1:
            return -1
        return 0
    except:
        return 0


def get_premarket_change(ticker_obj):
    try:
        df = ticker_obj.history(period="2d", prepost=True, interval="1h")
        if df is None or len(df) < 2:
            return 0
        today = datetime.now().date()
        today_rows = df[df.index.date == today]
        premarket = today_rows[today_rows.index.hour < 9]
        if premarket.empty:
            return 0
        premarket_last = premarket['Close'].iloc[-1]
        prev_close = df[df.index.date < today]['Close'].iloc[-1] if len(df[df.index.date < today]) > 0 else df['Close'].iloc[0]
        pm_change = ((premarket_last - prev_close) / prev_close) * 100
        return round(pm_change, 2)
    except:
        return 0


def get_risk_reward(df):
    try:
        last_close = df['Close'].iloc[-1]
        recent_high = df['High'].tail(10).max()
        recent_low = df['Low'].tail(10).min()
        target_dist = recent_high - last_close
        stop_dist = last_close - recent_low
        if stop_dist <= 0:
            return None
        return round(target_dist / stop_dist, 2)
    except:
        return None


def calculate_gap_percent(df):
    try:
        if len(df) < 2:
            return 0
        yesterday_close = df['Close'].iloc[-2]
        today_open = df['Open'].iloc[-1]
        gap = ((today_open - yesterday_close) / yesterday_close) * 100
        return round(gap, 2)
    except:
        return 0


def calculate_relative_volume(df):
    try:
        if len(df) < 6:
            return 1.0
        avg_vol = df['Volume'].iloc[-6:-1].mean()
        today_vol = df['Volume'].iloc[-1]
        if avg_vol == 0:
            return 1.0
        return round(today_vol / avg_vol, 2)
    except:
        return 1.0


def calculate_atr_percent(df):
    try:
        recent = df.tail(7)
        ranges = recent['High'] - recent['Low']
        atr = ranges.mean()
        last_close = df['Close'].iloc[-1]
        return round((atr / last_close) * 100, 2)
    except:
        return 0


def check_clean_technical_level(df):
    score = 0
    try:
        closes = df['Close']
        last_close = closes.iloc[-1]
        recent_high = df['High'].tail(10).max()
        if last_close >= recent_high * 0.98:
            score += 1
        if len(closes) >= 5:
            ma5 = closes.tail(5).mean()
            if last_close > ma5:
                score += 1
        day_high = df['High'].iloc[-1]
        day_low = df['Low'].iloc[-1]
        day_range = day_high - day_low
        if day_range > 0:
            close_position = (last_close - day_low) / day_range
            if close_position >= 0.75:
                score += 1
    except:
        pass
    return score


def check_news_catalyst(symbol):
    if not NEWS_API_KEY:
        return False
    try:
        url = f"https://newsapi.org/v2/everything?q={symbol}&sortBy=publishedAt&pageSize=3&apiKey={NEWS_API_KEY}"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        articles = data.get("articles", [])
        today = datetime.now().strftime("%Y-%m-%d")
        for article in articles:
            if article.get("publishedAt", "").startswith(today):
                return True
    except:
        pass
    return False


def score_stock(gap_pct, rvol, atr_pct, tech_score, has_catalyst, last_close,
                spy_modifier, sector_score, float_score, earnings_risky,
                rs_score, pm_change, rr_ratio):
    score = 0
    reasons = []

    # Gap (0-3)
    if 2 <= gap_pct <= 8:
        score += 3
        reasons.append(f"✅ Ideal gap ({gap_pct}%)")
    elif 8 < gap_pct <= 15:
        score += 2
        reasons.append(f"⚠️ Large gap ({gap_pct}%) — may be extended")
    elif 0.5 <= gap_pct < 2:
        score += 1
        reasons.append(f"🔹 Small gap ({gap_pct}%)")
    elif gap_pct < -2:
        score -= 1
        reasons.append(f"🔴 Gapping down ({gap_pct}%)")

    # Pre-market (0-2)
    if pm_change > 2:
        score += 2
        reasons.append(f"✅ Strong pre-market ({pm_change}%)")
    elif pm_change > 0.5:
        score += 1
        reasons.append(f"🔹 Pre-market positive ({pm_change}%)")
    elif pm_change < -1:
        score -= 1
        reasons.append(f"⚠️ Pre-market weak ({pm_change}%)")

    # RVOL (0-3)
    if rvol >= 3.0:
        score += 3
        reasons.append(f"✅ Very high relative volume ({rvol}x)")
    elif rvol >= 2.0:
        score += 2
        reasons.append(f"✅ High relative volume ({rvol}x)")
    elif rvol >= 1.5:
        score += 1
        reasons.append(f"🔹 Above average volume ({rvol}x)")
    else:
        reasons.append(f"⚠️ Low relative volume ({rvol}x)")

    # Technical (0-3)
    score += tech_score
    if tech_score == 3:
        reasons.append("✅ Strong technical setup")
    elif tech_score == 2:
        reasons.append("🔹 Decent technical setup")
    elif tech_score == 1:
        reasons.append("⚠️ Weak technical setup")

    # Catalyst (0-1)
    if has_catalyst:
        score += 1
        reasons.append("✅ News catalyst detected")

    # SPY (-2 to +2)
    score += spy_modifier
    if spy_modifier > 0:
        reasons.append("✅ Market bullish (SPY)")
    elif spy_modifier < 0:
        reasons.append("🔴 Market bearish (SPY) — caution")

    # Sector (-1 to +1)
    score += sector_score
    if sector_score > 0:
        reasons.append("✅ Sector showing strength")
    elif sector_score < 0:
        reasons.append("⚠️ Sector weak")

    # Float (-1 to +2)
    score += float_score
    if float_score == 2:
        reasons.append("✅ Low float — big move potential")
    elif float_score == 1:
        reasons.append("🔹 Moderate float")
    elif float_score < 0:
        reasons.append("⚠️ High float — harder to move")

    # Relative strength (-2 to +2)
    score += rs_score
    if rs_score > 0:
        reasons.append("✅ Outperforming SPY")
    elif rs_score < 0:
        reasons.append("⚠️ Underperforming SPY")

    # Earnings risk
    if earnings_risky:
        score -= 3
        reasons.append("🔴 EARNINGS WITHIN 3 DAYS — high risk")

    # Volatility
    if atr_pct < 1.5:
        score -= 1
        reasons.append(f"⚠️ Low volatility ({atr_pct}% ATR)")
    elif atr_pct >= 3:
        reasons.append(f"✅ Good daily range ({atr_pct}% ATR)")

    # Price
    if last_close < 5:
        score -= 2
        reasons.append("🔴 Price under $5 — too risky")
    elif last_close > 500:
        reasons.append("ℹ️ High price — size accordingly")

    # Risk/reward
    if rr_ratio is not None:
        if rr_ratio >= 2:
            score += 1
            reasons.append(f"✅ Good risk/reward ({rr_ratio}:1)")
        elif rr_ratio < 1:
            score -= 1
            reasons.append(f"⚠️ Poor risk/reward ({rr_ratio}:1)")

    # Normalize to 0-10
    normalized = round(min(10, max(0, (score / 16) * 10)), 1)
    return normalized, reasons


def save_scan_to_history(results):
    try:
        history = []
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE) as f:
                history = json.load(f)
        entry = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "picks": [{
                "symbol": r["symbol"],
                "score": r["score"],
                "grade": r["grade"],
                "last_close": r["last_close"],
                "outcome": None,
                "outcome_pct": None
            } for r in results]
        }
        history = [h for h in history if h["date"] != entry["date"]]
        history.append(entry)
        history = history[-90:]
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"Error saving history: {e}")


def update_outcomes():
    try:
        if not os.path.exists(HISTORY_FILE):
            return
        with open(HISTORY_FILE) as f:
            history = json.load(f)
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        for entry in history:
            if entry["date"] == yesterday:
                for pick in entry["picks"]:
                    if pick["outcome"] is None:
                        try:
                            _, df = safe_fetch(pick["symbol"], period="5d")
                            if df is not None and len(df) >= 1:
                                today_close = float(df['Close'].iloc[-1])
                                prev_close = pick["last_close"]
                                pct_change = round(((today_close - prev_close) / prev_close) * 100, 2)
                                pick["outcome"] = "win" if pct_change > 1 else "loss"
                                pick["outcome_pct"] = pct_change
                            time.sleep(0.5)
                        except:
                            pass
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"Error updating outcomes: {e}")


def calculate_win_rate():
    try:
        if not os.path.exists(HISTORY_FILE):
            return None
        with open(HISTORY_FILE) as f:
            history = json.load(f)
        all_picks = [p for entry in history for p in entry["picks"] if p["outcome"] is not None]
        if not all_picks:
            return None
        total = len(all_picks)
        wins = sum(1 for p in all_picks if p["outcome"] == "win")
        grade_stats = {}
        for grade in ["A", "B", "C"]:
            gp = [p for p in all_picks if p["grade"] == grade]
            if gp:
                gw = sum(1 for p in gp if p["outcome"] == "win")
                grade_stats[grade] = {"win_rate": round((gw / len(gp)) * 100, 1), "total": len(gp)}
        return {
            "overall_win_rate": round((wins / total) * 100, 1),
            "total_picks": total,
            "total_wins": wins,
            "by_grade": grade_stats
        }
    except:
        return None


def run_scanner():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting evening scanner v2.0...")

    update_outcomes()

    print("Checking SPY market condition...")
    spy_condition, spy_modifier = get_spy_condition()
    print(f"  Market: {spy_condition} (modifier: {spy_modifier:+d})")

    _, spy_df = safe_fetch("SPY", period="30d")

    results = []
    sector_cache = {}

    for i, symbol in enumerate(DEFAULT_WATCHLIST):
        try:
            print(f"[{i+1}/{len(DEFAULT_WATCHLIST)}] Scanning {symbol}...")
            time.sleep(0.5)

            ticker, df = safe_fetch(symbol, period="30d")
            if ticker is None or df is None or len(df) < 3:
                print(f"  Skipping {symbol} — insufficient data")
                continue

            last_close = float(df['Close'].iloc[-1])
            last_volume = int(df['Volume'].iloc[-1])

            gap_pct = calculate_gap_percent(df)
            rvol = calculate_relative_volume(df)
            atr_pct = calculate_atr_percent(df)
            tech_score = check_clean_technical_level(df)
            has_catalyst = check_news_catalyst(symbol)
            pm_change = get_premarket_change(ticker)
            sector_score, sector_etf = get_sector_strength(symbol, sector_cache)
            float_score, float_m = get_float_score(ticker)
            earnings_risky, days_to_earnings = get_earnings_risk(ticker)
            rs_score = get_relative_strength(df, spy_df)
            rr_ratio = get_risk_reward(df)

            score, reasons = score_stock(
                gap_pct, rvol, atr_pct, tech_score, has_catalyst, last_close,
                spy_modifier, sector_score, float_score, earnings_risky,
                rs_score, pm_change, rr_ratio
            )

            if score >= 3:
                results.append({
                    "symbol": symbol,
                    "score": score,
                    "last_close": round(last_close, 2),
                    "gap_pct": gap_pct,
                    "rvol": rvol,
                    "atr_pct": atr_pct,
                    "volume": last_volume,
                    "pm_change": pm_change,
                    "float_m": float_m,
                    "sector_etf": sector_etf,
                    "earnings_risky": earnings_risky,
                    "days_to_earnings": days_to_earnings,
                    "rs_score": rs_score,
                    "rr_ratio": rr_ratio,
                    "has_catalyst": has_catalyst,
                    "tech_score": tech_score,
                    "reasons": reasons,
                    "grade": "A" if score >= 8 else "B" if score >= 6 else "C"
                })

        except Exception as e:
            print(f"  Error scanning {symbol}: {e}")
            continue

    results.sort(key=lambda x: x['score'], reverse=True)
    results = results[:15]

    win_rate_data = calculate_win_rate()

    output = {
        "timestamp": datetime.now().isoformat(),
        "market_date": (datetime.now() + timedelta(days=1)).strftime("%A, %B %d %Y"),
        "total_scanned": len(DEFAULT_WATCHLIST),
        "market_condition": spy_condition,
        "spy_modifier": spy_modifier,
        "win_rate": win_rate_data,
        "results": results
    }

    with open(RESULTS_FILE, "w") as f:
        json.dump(output, f, indent=2)

    save_scan_to_history(results)

    print(f"✅ Scan complete. Found {len(results)} setups. Market: {spy_condition}")
    if win_rate_data:
        print(f"📊 Win rate: {win_rate_data['overall_win_rate']}% ({win_rate_data['total_picks']} picks tracked)")

    return output


if __name__ == "__main__":
    run_scanner()
