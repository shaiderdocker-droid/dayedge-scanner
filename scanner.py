"""
Evening Stock Scanner - Day Trading Setup
Uses yfinance for free market data (no subscription needed)
"""

import os
import json
import requests
import yfinance as yf
from datetime import datetime, timedelta

NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")

DEFAULT_WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "GOOGL", "AMZN",
    "NFLX", "CRM", "PLTR", "SOFI", "RIVN", "LCID", "NIO", "BABA",
    "COIN", "HOOD", "MARA", "RIOT", "UPST", "AFRM", "SQ", "PYPL",
    "SNAP", "UBER", "LYFT", "ABNB", "DASH", "RBLX", "SHOP", "SPOT",
    "ZM", "ROKU", "TWLO", "DDOG", "NET", "CRWD", "OKTA", "SNOW"
]

def get_stock_data(symbol, period="30d"):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period)
        return df
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
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

def score_stock(symbol, gap_pct, rvol, atr_pct, tech_score, has_catalyst, last_close):
    score = 0
    reasons = []

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

    score += tech_score
    if tech_score == 3:
        reasons.append("✅ Strong technical setup")
    elif tech_score == 2:
        reasons.append("🔹 Decent technical setup")
    elif tech_score == 1:
        reasons.append("⚠️ Weak technical setup")

    if has_catalyst:
        score += 1
        reasons.append("✅ News catalyst detected today")

    if atr_pct < 1.5:
        score -= 1
        reasons.append(f"⚠️ Low volatility ({atr_pct}% ATR)")
    elif atr_pct >= 3:
        reasons.append(f"✅ Good daily range ({atr_pct}% ATR)")

    if last_close < 5:
        score -= 2
        reasons.append("🔴 Price under $5 — too risky")
    elif last_close > 500:
        reasons.append("ℹ️ High price — size accordingly")

    return max(0, score), reasons

def run_scanner():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting evening scanner...")
    results = []

    for symbol in DEFAULT_WATCHLIST:
        try:
            print(f"Scanning {symbol}...")
            df = get_stock_data(symbol, period="30d")
            if df is None or len(df) < 3:
                continue

            last_close = df['Close'].iloc[-1]
            last_volume = df['Volume'].iloc[-1]
            gap_pct = calculate_gap_percent(df)
            rvol = calculate_relative_volume(df)
            atr_pct = calculate_atr_percent(df)
            tech_score = check_clean_technical_level(df)
            has_catalyst = check_news_catalyst(symbol)

            score, reasons = score_stock(
                symbol, gap_pct, rvol, atr_pct, tech_score, has_catalyst, last_close
            )

            if score >= 3:
                results.append({
                    "symbol": symbol,
                    "score": score,
                    "last_close": round(float(last_close), 2),
                    "gap_pct": gap_pct,
                    "rvol": rvol,
                    "atr_pct": atr_pct,
                    "volume": int(last_volume),
                    "has_catalyst": has_catalyst,
                    "tech_score": tech_score,
                    "reasons": reasons,
                    "grade": "A" if score >= 8 else "B" if score >= 6 else "C"
                })
        except Exception as e:
            print(f"Error scanning {symbol}: {e}")
            continue

    results.sort(key=lambda x: x['score'], reverse=True)
    results = results[:15]

    output = {
        "timestamp": datetime.now().isoformat(),
        "market_date": (datetime.now() + timedelta(days=1)).strftime("%A, %B %d %Y"),
        "total_scanned": len(DEFAULT_WATCHLIST),
        "results": results
    }

    with open("scan_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"✅ Scan complete. Found {len(results)} setups.")
    return output

if __name__ == "__main__":
    run_scanner()
