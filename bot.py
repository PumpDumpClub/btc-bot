import os
import asyncio
import aiohttp
from datetime import datetime
from telegram import Bot

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")
SCHEDULE_MIN   = 15
INTERVAL       = "15"
LIMIT          = 50

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT"
]

async def fetch(url, params):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                return await r.json(content_type=None)
    except Exception as e:
        print(f"[FETCH ERROR] {url} — {e}")
        return None

async def get_klines(symbol):
    data = await fetch("https://api.bybit.com/v5/market/kline",
        {"category": "linear", "symbol": symbol, "interval": INTERVAL, "limit": LIMIT})
    if not data or "result" not in data:
        return None, None, None, None
    candles = data["result"]["list"]
    closes  = [float(c[4]) for c in reversed(candles)]
    highs   = [float(c[2]) for c in reversed(candles)]
    lows    = [float(c[3]) for c in reversed(candles)]
    volumes = [float(c[5]) for c in reversed(candles)]
    return closes, highs, lows, volumes

async def get_funding(symbol):
    data = await fetch("https://api.bybit.com/v5/market/funding/history",
        {"category": "linear", "symbol": symbol, "limit": 1})
    if not data or "result" not in data:
        return 0
    return float(data["result"]["list"][0]["fundingRate"]) * 100

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

async def analyze(symbol):
    closes, highs, lows, volumes = await get_klines(symbol)
    if closes is None:
        return {"symbol": symbol, "error": "Gagal ambil data"}
    funding  = await get_funding(symbol)
    price    = closes[-1]
    ma7      = ma(closes, 7)
    ma14     = ma(closes, 14)
    ma28     = ma(closes, 28)
    rsi_v    = rsi(closes)
    avg_vol  = sum(volumes[-10:]) / 10
    vol_surge = volumes[-1] > avg_vol * 1.5

    signal    = "⚪ WAIT"
    direction = "TUNGGU"
    confidence = "Rendah"
    tp_pct    = 1.0
    sl_pct    = 0.5

    if price > ma7 > ma14 > ma28 and rsi_v < 70 and funding > -0.01:
        signal, direction = "🟢 LONG", "LONG"
        confidence = "Tinggi" if vol_surge else "Medium"
        tp_pct = 1.5 if vol_surge else 1.0
    elif price < ma7 < ma14 < ma28 and rsi_v > 30 and funding < 0.01:
        signal, direction = "🔴 SHORT", "SHORT"
        confidence = "Tinggi" if vol_surge else "Medium"
        tp_pct = 1.5 if vol_surge else 1.0
    elif rsi_v > 75:
        signal, direction, confidence = "🔴 SHORT", "SHORT", "Medium"
    elif rsi_v < 25:
        signal, direction, confidence = "🟢 LONG", "LONG", "Medium"

    if direction in ("LONG", "SHORT"):
        entry = round(price, 4)
        sl = round(price * (1 - sl_pct/100) if direction == "LONG" else price * (1 + sl_pct/100), 4)
        tp = round(price * (1 + tp_pct/100) if direction == "LONG" else price * (1 - tp_pct/100), 4)
    else:
        entry = sl = tp = 0

    return {"symbol": symbol, "price": round(price, 4), "signal": signal,
            "direction": direction, "entry": entry, "sl": sl, "tp": tp,
            "confidence": confidence, "rsi": round(rsi_v, 1),
            "funding": round(funding, 4), "vol_surge": vol_surge}

def format_one(a):
    if "error" in a:
        return f"⚠️ {a['symbol']}: {a['error']}\n"
    vol = " ⚡" if a["vol_surge"] else ""
    if a["entry"] == 0:
        return (f"{a['signal']} {a['symbol']}\n"
                f"💰 {a['price']} | RSI: {a['rsi']} | Fund: {a['funding']}%\n"
                f"❌ Tunggu konfirmasi\n")
    return (f"{a['signal']} {a['symbol']}{vol}\n"
            f"💰 {a['price']} | RSI: {a['rsi']} | Fund: {a['funding']}%\n"
            f"📍 Entry: {a['entry']}  🎯 TP: {a['tp']}  🛑 SL: {a['sl']}\n"
            f"🎖 {a['confidence']}\n")

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    print(f"✅ Bot started — {len(SYMBOLS)} koin, tiap {SCHEDULE_MIN} menit")
    while True:
        try:
            now     = datetime.utcnow().strftime("%H:%M UTC")
            results = []
            for symbol in SYMBOLS:
                r = await analyze(symbol)
                results.append(r)
                await asyncio.sleep(2)

            results.sort(key=lambda x: 0 if x.get("direction") in ("LONG","SHORT") else 1)

            msg = f"📊 Sinyal {now}\n{'━'*22}\n"
            for r in results:
                msg += format_one(r) + "\n"
            msg += "⚠️ Bukan financial advice. Selalu pakai SL!"

            await bot.send_message(chat_id=CHAT_ID, text=msg)
            print(f"[OK] Sinyal terkirim — {now}")
        except Exception as e:
            print(f"[ERROR] {e}")
        await asyncio.sleep(SCHEDULE_MIN * 60)

if __name__ == "__main__":
    asyncio.run(main())
