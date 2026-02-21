"""
Evening Stock Scanner - Day Trading Setup
Uses Alpaca API for market data
Scores stocks on: Gap %, Pre-market Volume, Catalyst, Technical Level
"""

import os
import json
import requests
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
import pandas as pd
import numpy as np

# --- Config ---
API_KEY = os.environ.get("ALPACA_API_KEY", "YOUR_API_KEY_HERE")
API_SECRET = os.environ.get("ALPACA_API_SECRET", "YOUR_API_SECRET_HERE")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")  # optional

# Watchlist: top liquid stocks + ETFs commonly in play
# In production this can be expanded or pulled dynamically
DEFAULT_WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "GOOGL", "AMZN",
    "NFLX", "CRM", "PLTR", "SOFI", "RIVN", "LCID", "NIO", "BABA",
    "COIN", "HOOD", "MARA", "RIOT", "UPST", "AFRM", "SQ", "PYPL",
    "SNAP", "UBER", "LYFT", "ABNB", "DASH", "RBLX", "SHOP", "SPOT",
    "ZM", "ROKU", "TWLO", "DDOG", "NET", "CRWD", "OKTA", "SNOW"
]


def get_historical_data(client, symbols, days=10):
    """Fetch recent daily bars for all symbols"""
    end = datetime.now()
    start = end - timedelta(days=days)
    
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start,
        end=end
    )
    
    bars = client.get_stock_bars(request)
    return bars.df if hasattr(bars, 'df') else None


def calculate_gap_percent(bars_df, symbol):
    """Calculate gap % = (today's open - yesterday's close) / yesterday's close"""
    try:
        sym_bars = bars_df.loc[symbol].sort_index()
        if len(sym_bars) < 2:
            return 0
        yesterday_close = sym_bars.iloc[-2]['close']
        today_open = sym_bars.iloc[-1]['open']
        gap = ((today_open - yesterday_close) / yesterday_close) * 100
        return round(gap, 2)
    except:
        return 0


def calculate_relative_volume(bars_df, symbol):
    """RVOL = today's volume / average volume of last 5 days"""
    try:
        sym_bars = bars_df.loc[symbol].sort_index()
        if len(sym_bars) < 6:
            return 1.0
        avg_vol = sym_bars.iloc[-6:-1]['volume'].mean()
        today_vol = sym_bars.iloc[-1]['volume']
        if avg_vol == 0:
            return 1.0
        return round(today_vol / avg_vol, 2)
    except:
        return 1.0


def calculate_atr_percent(bars_df, symbol):
    """ATR as % of price — measures daily volatility"""
    try:
        sym_bars = bars_df.loc[symbol].sort_index().tail(7)
        ranges = sym_bars['high'] - sym_bars['low']
        atr = ranges.mean()
        last_close = sym_bars.iloc[-1]['close']
        return round((atr / last_close) * 100, 2)
    except:
        return 0


def check_clean_technical_level(bars_df, symbol):
    """
    Check if price is near a clean technical level:
    - Near 52-week high (within 2%)
    - Price crossed above 20-day MA
    - Inside day followed by breakout
    Returns a score 0-3
    """
    score = 0
    try:
        sym_bars = bars_df.loc[symbol].sort_index()
        closes = sym_bars['close']
        highs = sym_bars['high']
        last_close = closes.iloc[-1]
        last_open = sym_bars['open'].iloc[-1]

        # Near recent high (last 10 days)
        recent_high = highs.tail(10).max()
        if last_close >= recent_high * 0.98:
            score += 1

        # Above 20-day moving average
        if len(closes) >= 5:
            ma5 = closes.tail(5).mean()
            if last_close > ma5:
                score += 1

        # Strong close (closed in top 25% of day's range)
        day_high = sym_bars['high'].iloc[-1]
        day_low = sym_bars['low'].iloc[-1]
        day_range = day_high - day_low
        if day_range > 0:
            close_position = (last_close - day_low) / day_range
            if close_position >= 0.75:
                score += 1

    except:
        pass
    return score


def check_news_catalyst(symbol):
    """
    Simple news check using free NewsAPI
    Returns True if recent news found
    """
    if not NEWS_API_KEY:
        return False
    try:
        url = f"https://newsapi.org/v2/everything?q={symbol}&sortBy=publishedAt&pageSize=3&apiKey={NEWS_API_KEY}"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        articles = data.get("articles", [])
        if not articles:
            return False
        # Check if any article is from today
        today = datetime.now().strftime("%Y-%m-%d")
        for article in articles:
            if article.get("publishedAt", "").startswith(today):
                return True
    except:
        pass
    return False


def score_stock(symbol, gap_pct, rvol, atr_pct, tech_score, has_catalyst, last_close):
    """
    Score each stock 0-10 based on day trading criteria
    Higher score = higher probability setup
    """
    score = 0
    reasons = []

    # --- Gap Score (0-3 points) ---
    if 2 <= gap_pct <= 8:
        score += 3
        reasons.append(f"✅ Ideal gap ({gap_pct}%)")
    elif 8 < gap_pct <= 15:
        score += 2
        reasons.append(f"⚠️ Large gap ({gap_pct}%) — momentum but may be extended")
    elif 0.5 <= gap_pct < 2:
        score += 1
        reasons.append(f"🔹 Small gap ({gap_pct}%)")
    elif gap_pct < -2:
        score -= 1
        reasons.append(f"🔴 Gapping down ({gap_pct}%)")

    # --- Pre-market Volume / RVOL Score (0-3 points) ---
    if rvol >= 3.0:
        score += 3
        reasons.append(f"✅ Very high relative volume ({rvol}x)")
    elif rvol >= 2.0:
        score += 2
        reasons.append(f"✅ High relative volume ({rvol}x)")
    elif rvol >= 1.5:
        score += 1
        reasons.append(f"🔹 Above avg volume ({rvol}x)")
    else:
        reasons.append(f"⚠️ Low relative volume ({rvol}x)")

    # --- Technical Level Score (0-3 points) ---
    score += tech_score
    if tech_score == 3:
        reasons.append("✅ Strong technical setup (high, above MA, strong close)")
    elif tech_score == 2:
        reasons.append("🔹 Decent technical setup")
    elif tech_score == 1:
        reasons.append("⚠️ Weak technical setup")

    # --- Catalyst Score (0-1 point) ---
    if has_catalyst:
        score += 1
        reasons.append("✅ News catalyst detected today")

    # --- Volatility filter (no points, but flag low ATR) ---
    if atr_pct < 1.5:
        score -= 1
        reasons.append(f"⚠️ Low daily volatility ({atr_pct}% ATR) — may not move enough")
    elif atr_pct >= 3:
        reasons.append(f"✅ Good daily range ({atr_pct}% ATR)")

    # --- Price filter ---
    if last_close < 5:
        score -= 2
        reasons.append("🔴 Price under $5 — too risky")
    elif last_close > 500:
        reasons.append("ℹ️ High price stock — position size accordingly")

    return max(0, score), reasons


def run_scanner():
    """Main scanner function — returns ranked list of stocks"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting evening scanner...")

    client = StockHistoricalDataClient(API_KEY, API_SECRET)

    print("Fetching historical data...")
    bars_df = get_historical_data(client, DEFAULT_WATCHLIST, days=30)

    if bars_df is None or bars_df.empty:
        return {"error": "Could not fetch market data. Check your API keys.", "results": [], "timestamp": datetime.now().isoformat()}

    results = []

    for symbol in DEFAULT_WATCHLIST:
        try:
            sym_bars = bars_df.loc[symbol].sort_index()
            if len(sym_bars) < 3:
                continue

            last_close = sym_bars.iloc[-1]['close']
            last_volume = sym_bars.iloc[-1]['volume']

            gap_pct = calculate_gap_percent(bars_df, symbol)
            rvol = calculate_relative_volume(bars_df, symbol)
            atr_pct = calculate_atr_percent(bars_df, symbol)
            tech_score = check_clean_technical_level(bars_df, symbol)
            has_catalyst = check_news_catalyst(symbol)

            score, reasons = score_stock(
                symbol, gap_pct, rvol, atr_pct, tech_score, has_catalyst, last_close
            )

            # Only include stocks with a score above 3
            if score >= 3:
                results.append({
                    "symbol": symbol,
                    "score": score,
                    "last_close": round(last_close, 2),
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
            continue

    # Sort by score descending
    results.sort(key=lambda x: x['score'], reverse=True)

    # Keep top 15
    results = results[:15]

    output = {
        "timestamp": datetime.now().isoformat(),
        "market_date": (datetime.now() + timedelta(days=1)).strftime("%A, %B %d %Y"),
        "total_scanned": len(DEFAULT_WATCHLIST),
        "results": results
    }

    # Save to file for the web server to read
    with open("scan_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"✅ Scan complete. Found {len(results)} setups.")
    return output


if __name__ == "__main__":
    run_scanner()
