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
    recent = [f for f in fractals if f >= min(lows[-lookback:])]
    return min(recent) if recent else min(lows[-lookback:])

def find_fractal_highs(highs, lookback=40):
    fractals = []
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
           highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            fractals.append(highs[i])
    recent = [f for f in fractals if f <= max(highs[-lookback:])]
    return max(recent) if recent else max(highs[-lookback:])

def structure_break_long(closes, highs, lows):
    prev_high    = max(highs[-5:-2])
    broke        = closes[-1] > prev_high
    body         = abs(closes[-1] - closes[-2])
    rng          = highs[-1] - lows[-1] if highs[-1] != lows[-1] else 0.0001
    return broke and (body / rng > 0.55) and (closes[-2] > closes[-3])

def structure_break_short(closes, lows, highs):
    prev_low     = min(lows[-5:-2])
    broke        = closes[-1] < prev_low
    body         = abs(closes[-1] - closes[-2])
    rng          = highs[-1] - lows[-1] if highs[-1] != lows[-1] else 0.0001
    return broke and (body / rng > 0.55) and (closes[-2] < closes[-3])

# ══════════════════════════════════════════
# P5 — POSITION SIZING
# ══════════════════════════════════════════

def position_size(account, risk_pct, sl_pct):
    # Berapa USD yang harus dimasukkan supaya loss = 1% akun
    # position_size = (account * risk_pct) / sl_pct
    if sl_pct <= 0:
        return 0
    return round((account * risk_pct) / (sl_pct / 100), 2)

# ══════════════════════════════════════════
# AUTO TRACKER
# ══════════════════════════════════════════

async def check_open_trades(bot):
    open_trades = load_open_trades()
    if not open_trades:
        return

    for trade in open_trades:
        symbol    = trade["symbol"]
        direction = trade["direction"]
        entry     = float(trade["entry"])
        sl        = float(trade["sl"])
        tp        = float(trade["tp"])
        bars      = int(trade.get("bars_held", 0)) + 1
        timestamp = trade["timestamp"]
        pos_size  = float(trade.get("position_size_usd", 0))

        # P1: Track highest/lowest sejak entry
        prev_high = float(trade.get("highest_since_entry", entry))
        prev_low  = float(trade.get("lowest_since_entry",  entry))

        price = await get_price(symbol)
        if price is None:
            continue

        # Update high/low tracker
        new_high = max(prev_high, price)
        new_low  = min(prev_low,  price)

        # P1: MAE / MFE real dari highest/lowest
        if direction == "LONG":
            mae = round((entry - new_low)  / entry * 100, 3)
            mfe = round((new_high - entry) / entry * 100, 3)
        else:
            mae = round((new_high - entry) / entry * 100, 3)
            mfe = round((entry - new_low)  / entry * 100, 3)

        mae = max(0, mae)
        mfe = max(0, mfe)

        result = None
        pnl    = 0

        if direction == "LONG":
            if price >= tp:
                result = "WIN"
                pnl    = round((tp - entry) / entry * 100, 3)
            elif price <= sl:
                result = "LOSS"
                pnl    = round((sl - entry) / entry * 100, 3)
        else:
            if price <= tp:
                result = "WIN"
                pnl    = round((entry - tp) / entry * 100, 3)
            elif price >= sl:
                result = "LOSS"
                pnl    = round((entry - sl) / entry * 100, 3)

        if bars >= 48 and not result:
            result = "EXPIRED"
            pnl    = round((price - entry) / entry * 100 *
                           (1 if direction == "LONG" else -1), 3)

        if result:
            pnl_usd = round(pos_size * pnl / 100, 2)
            update_trade(timestamp, symbol, {
                "result"             : result,
                "pnl_pct"            : pnl,
                "highest_since_entry": new_high,
                "lowest_since_entry" : new_low,
                "mae_pct"            : mae,
                "mfe_pct"            : mfe,
                "bars_held"          : bars,
            })
            emoji = "✅" if result == "WIN" else ("❌" if result == "LOSS" else "⏰")
            sl_tight = mae > 0 and mae < float(trade["sl_pct"]) * 0.5
            tp_far   = mfe > float(trade["tp_pct"]) * 0.8 and result != "WIN"

            insight = ""
            if sl_tight:
                insight += "💡 SL mungkin terlalu sempit\n"
            if tp_far:
                insight += "💡 TP hampir kena tapi miss — pertimbangkan RR lebih kecil\n"

            msg = (
                f"{emoji} TRADE SELESAI — #{symbol}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Direction : {direction}\n"
                f"Result    : {result}\n"
                f"PnL       : {'+' if pnl > 0 else ''}{pnl}%\n"
                f"PnL (USD) : {'+' if pnl_usd > 0 else ''}${pnl_usd}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"MAE       : -{mae}% (max drawdown)\n"
                f"MFE       : +{mfe}% (max profit seen)\n"
                f"Durasi    : {bars * 15} menit\n"
                f"{insight}"
            )
            await bot.send_message(chat_id=CHAT_ID, text=msg)
            await asyncio.sleep(1)
        else:
            update_trade(timestamp, symbol, {
                "highest_since_entry": new_high,
                "lowest_since_entry" : new_low,
                "mae_pct"            : mae,
                "mfe_pct"            : mfe,
                "bars_held"          : bars,
            })

# ══════════════════════════════════════════
# CORE ANALYZE
# ══════════════════════════════════════════

async def analyze(symbol):
    # P2: skip kalau ada open trade di symbol ini
    if has_open_trade(symbol):
        return None

    (closes, highs, lows, volumes), (c_htf, _, _, _) = await asyncio.gather(
        get_klines(symbol, INTERVAL_LTF),
        get_klines(symbol, INTERVAL_HTF)
    )

    if closes is None or c_htf is None:
        return None
    if len(closes) < 100 or len(c_htf) < 50:
        return None

    price = closes[-1]
    ma20  = ma(closes, 20)
    ma50  = ma(closes, 50)
    ma99  = ma(closes, 99)
    atr_v = atr(highs, lows, closes)
    rsi_v = rsi(closes)
    vol   = volume_surge(volumes)

    regime = market_regime(closes, highs, lows, atr_v)
    if regime != "TRENDING":
        return None

    ma50_htf = ma(c_htf, 50)
    bias     = htf_bias(c_htf, ma50_htf)
    if bias == "NEUTRAL":
        return None

    trend_up, trend_down = is_trending(ma20, ma50, ma99)
    if not trend_up and not trend_down:
        return None
    if is_overextended(price, ma50, atr_v):
        return None
    if bias == "UP" and not trend_up:
        return None
    if bias == "DOWN" and not trend_down:
        return None

    direction = None
    if trend_up and bias == "UP":
        if (price <= ma50 * 1.012 and 38 <= rsi_v <= 55 and
                vol >= 1.2 and structure_break_long(closes, highs, lows)):
            direction = "LONG"
    if trend_down and bias == "DOWN":
        if (price >= ma50 * 0.988 and 45 <= rsi_v <= 62 and
                vol >= 1.2 and structure_break_short(closes, lows, highs)):
            direction = "SHORT"

    if not direction:
        return None

    # SL fractal
    if direction == "LONG":
        sl = round(find_fractal_lows(lows) - atr_v * 0.2, 6)
    else:
        sl = round(find_fractal_highs(highs) + atr_v * 0.2, 6)

    risk = abs(price - sl)
    if risk <= 0 or risk > price * 0.04:
        return None

    # P4: Simulasi slippage
    if direction == "LONG":
        entry = round(price * (1 + SLIPPAGE), 6)
    else:
        entry = round(price * (1 - SLIPPAGE), 6)

    tp     = round(entry + risk * 2.0 if direction == "LONG"
                   else entry - risk * 2.0, 6)
    sl_pct = round(abs(entry - sl) / entry * 100, 2)
    tp_pct = round(abs(tp - entry) / entry * 100, 2)
    rr     = 2.0

    # P5: Position sizing
    pos_size = position_size(ACCOUNT_SIZE, RISK_PCT, sl_pct)

    candles_est = max(4, min(round(tp_pct / (atr_v / price * 100) * 1.5), 24))

    return {
        "symbol"          : symbol,
        "direction"       : direction,
        "price"           : round(price, 6),
        "entry"           : entry,
        "sl"              : sl,
        "tp"              : tp,
        "sl_pct"          : sl_pct,
        "tp_pct"          : tp_pct,
        "rr"              : rr,
        "rsi"             : round(rsi_v, 1),
        "vol"             : round(vol, 1),
        "bias"            : bias,
        "regime"          : regime,
        "menit"           : candles_est * 15,
        "position_size_usd": pos_size,
    }

# ══════════════════════════════════════════
# FORMAT & MAIN
# ══════════════════════════════════════════

def format_signal(a):
    emoji  = "🚀" if a["direction"] == "LONG" else "🔻"
    bias_e = "⬆️" if a["bias"] == "UP" else "⬇️"
    jam    = a["menit"] // 60
    durasi = f"{jam}j {a['menit']%60}m" if jam > 0 else f"{a['menit']}m"
    return (
        f"{emoji} {a['direction']} #{a['symbol']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Harga  : {a['price']}\n"
        f"📍 Entry  : {a['entry']}\n"
        f"🎯 TP     : {a['tp']} (+{a['tp_pct']}%)\n"
        f"🛑 SL     : {a['sl']} (-{a['sl_pct']}%)\n"
        f"💡 RR     : 1:{a['rr']}\n"
        f"💵 Size   : ${a['position_size_usd']} (risk 1%)\n"
        f"⏱ Berlaku: ±{durasi}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 RSI    : {a['rsi']}\n"
        f"📈 Volume : {a['vol']}x\n"
        f"{bias_e} HTF    : {a['bias']}\n"
    )

async def main():
    init_log()
    bot   = Bot(token=TELEGRAM_TOKEN)
    cycle = 0
    print("✅ Bot FINAL — RealMAE/MFE + 1TradePerSymbol + Slippage + Sizing")

    while True:
        try:
            now    = datetime.now(timezone.utc).strftime("%H:%M UTC")
            cycle += 1

            await check_open_trades(bot)

            signals = []
            for symbol in SYMBOLS:
                result = await analyze(symbol)
                if result:
                    signals.append(result)
                    log_signal(result)
                await asyncio.sleep(2)

            if signals:
                msg = f"🔥 SINYAL — {now}\n\n"
                for s in signals:
                    msg += format_signal(s) + "\n"
                msg += "⚠️ Risk 1% per trade. Pasang SL!"
            else:
                msg = (f"🕐 Jam : {now}\n"
                       f"Belum ada sinyal kuat bosku.\n"
                       f"Sedang mencariii 🔥")

            if cycle % 96 == 0:
                stats = get_stats()
                if stats:
                    exp_e = "✅" if stats["expectancy"] > 0 else "❌"
                    msg  += (
                        f"\n\n📊 STATISTIK HARIAN\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Total    : {stats['total']}\n"
                        f"WIN      : {stats['wins']}\n"
                        f"LOSS     : {stats['losses']}\n"
                        f"Winrate  : {stats['winrate']}%\n"
                        f"Avg WIN  : +{stats['avg_win']}%\n"
                        f"Avg LOSS : {stats['avg_loss']}%\n"
                        f"Avg MAE  : -{stats['avg_mae']}%\n"
                        f"Avg MFE  : +{stats['avg_mfe']}%\n"
                        f"{exp_e} Expectancy: {stats['expectancy']}%\n"
                    )

            await bot.send_message(chat_id=CHAT_ID, text=msg)
            print(f"[OK] {now} — {len(signals)} sinyal")

        except Exception as e:
            print(f"[ERROR] {e}")

        await asyncio.sleep(SCHEDULE_MIN * 60)

if __name__ == "__main__":
    asyncio.run(main())
