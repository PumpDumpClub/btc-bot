import os
import asyncio
import aiohttp
import csv
from datetime import datetime, timezone
from telegram import Bot

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")
SCHEDULE_MIN   = 15
INTERVAL_LTF   = "15"
INTERVAL_HTF   = "60"
LIMIT          = 150
LOG_FILE       = "trade_log.csv"
SLIPPAGE       = 0.0005   # 0.05% simulasi slippage
RISK_PCT       = 0.01     # Risk 1% per trade
ACCOUNT_SIZE   = float(os.environ.get("ACCOUNT_SIZE", "100"))  # USD

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT"
]

# ══════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════

FIELDNAMES = [
    "timestamp", "symbol", "direction",
    "entry", "sl", "tp",
    "sl_pct", "tp_pct", "rr",
    "rsi", "volume", "htf_bias", "regime",
    "result", "pnl_pct",
    "highest_since_entry",  # P1: track real MFE
    "lowest_since_entry",   # P1: track real MAE
    "mae_pct", "mfe_pct",
    "bars_held",
    "position_size_usd"     # P5: position sizing
]

def init_log():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()

def log_signal(a):
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerow({
            "timestamp"          : datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "symbol"             : a["symbol"],
            "direction"          : a["direction"],
            "entry"              : a["entry"],
            "sl"                 : a["sl"],
            "tp"                 : a["tp"],
            "sl_pct"             : a["sl_pct"],
            "tp_pct"             : a["tp_pct"],
            "rr"                 : a["rr"],
            "rsi"                : a["rsi"],
            "volume"             : a["vol"],
            "htf_bias"           : a["bias"],
            "regime"             : a["regime"],
            "result"             : "OPEN",
            "pnl_pct"            : "",
            "highest_since_entry": a["entry"],  # mulai dari entry
            "lowest_since_entry" : a["entry"],  # mulai dari entry
            "mae_pct"            : 0,
            "mfe_pct"            : 0,
            "bars_held"          : 0,
            "position_size_usd"  : a["position_size_usd"],
        })

def load_open_trades():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r") as f:
        return [dict(r) for r in csv.DictReader(f) if r["result"] == "OPEN"]

def has_open_trade(symbol):
    # P2: limit 1 trade per symbol
    return any(t["symbol"] == symbol for t in load_open_trades())

def update_trade(timestamp, symbol, updates):
    rows = []
    with open(LOG_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["timestamp"] == timestamp and row["symbol"] == symbol:
                row.update(updates)
            rows.append(row)
    with open(LOG_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

def get_stats():
    if not os.path.exists(LOG_FILE):
        return None
    with open(LOG_FILE, "r") as f:
        rows = [r for r in csv.DictReader(f) if r["result"] in ("WIN", "LOSS", "EXPIRED")]
    if not rows:
        return None

    wins   = [float(r["pnl_pct"]) for r in rows if r["result"] == "WIN"]
    losses = [float(r["pnl_pct"]) for r in rows if r["result"] in ("LOSS", "EXPIRED")]
    maes   = [float(r["mae_pct"]) for r in rows if r["mae_pct"]]
    mfes   = [float(r["mfe_pct"]) for r in rows if r["mfe_pct"]]

    total    = len(rows)
    avg_win  = round(sum(wins)   / len(wins),   3) if wins   else 0
    avg_loss = round(sum(losses) / len(losses), 3) if losses else 0
    wr       = len(wins) / total

    # Expectancy = (WR * avg_win) + ((1-WR) * avg_loss)
    expectancy = round(wr * avg_win + (1 - wr) * avg_loss, 3)

    return {
        "total"      : total,
        "wins"       : len(wins),
        "losses"     : len(losses),
        "winrate"    : round(wr * 100, 1),
        "avg_win"    : avg_win,
        "avg_loss"   : avg_loss,
        "avg_mae"    : round(sum(maes) / len(maes), 3) if maes else 0,
        "avg_mfe"    : round(sum(mfes) / len(mfes), 3) if mfes else 0,
        "expectancy" : expectancy,
    }

# ══════════════════════════════════════════
# DATA FETCH
# ══════════════════════════════════════════

async def fetch(url, params):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params,
                           timeout=aiohttp.ClientTimeout(total=15),
                           ssl=False) as r:
                return await r.json(content_type=None)
    except Exception as e:
        print(f"[FETCH ERROR] {e}")
        return None

async def get_klines(symbol, interval):
    data = await fetch("https://api.bybit.com/v5/market/kline",
        {"category": "linear", "symbol": symbol,
         "interval": interval, "limit": LIMIT})
    if not data or "result" not in data:
        return None, None, None, None
    candles = data["result"]["list"]
    closes  = [float(c[4]) for c in reversed(candles)]
    highs   = [float(c[2]) for c in reversed(candles)]
    lows    = [float(c[3]) for c in reversed(candles)]
    volumes = [float(c[5]) for c in reversed(candles)]
    return closes, highs, lows, volumes

async def get_price(symbol):
    data = await fetch("https://api.bybit.com/v5/market/tickers",
        {"category": "linear", "symbol": symbol})
    if not data or "result" not in data:
        return None
    return float(data["result"]["list"][0]["lastPrice"])

# ══════════════════════════════════════════
# INDIKATOR
# ══════════════════════════════════════════

def ma(data, period):
    return sum(data[-period:]) / period if len(data) >= period else 0

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[-period + i] - closes[-period + i - 1]
        (gains if diff > 0 else losses).append(abs(diff))
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.0001
    return 100 - (100 / (1 + avg_gain / avg_loss))

def atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, min(period + 1, len(closes))):
        tr = max(highs[-i] - lows[-i],
                 abs(highs[-i] - closes[-i-1]),
                 abs(lows[-i]  - closes[-i-1]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0

def volume_surge(volumes):
    if len(volumes) < 20:
        return 1
    return volumes[-1] / (sum(volumes[-20:-1]) / 19)

# ══════════════════════════════════════════
# MARKET STRUCTURE
# ══════════════════════════════════════════

def market_regime(closes, highs, lows, atr_v):
    price_range = max(highs[-20:]) - min(lows[-20:])
    choppiness  = (atr_v * 20) / price_range if price_range > 0 else 1
    atr_avg     = atr(highs, lows, closes, period=50)
    if atr_v > atr_avg * 2.0:
        return "SPIKE"
    elif choppiness > 0.75:
        return "SIDEWAYS"
    return "TRENDING"

def htf_bias(closes_htf, ma50_htf):
    price = closes_htf[-1]
    if price > ma50_htf * 1.002:
        return "UP"
    elif price < ma50_htf * 0.998:
        return "DOWN"
    return "NEUTRAL"

def is_trending(ma20, ma50, ma99):
    return ma20 > ma50 > ma99, ma20 < ma50 < ma99

def is_overextended(price, ma50, atr_v):
    return abs(price - ma50) > atr_v * 3

def find_fractal_lows(lows, lookback=40):
    fractals = []
    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
           lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            fractals.append(lows[i])
    recent = [f
