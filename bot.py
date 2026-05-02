import os
import asyncio
import aiohttp
import json
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

async def fetch_json(url, params):
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers={"Accept": "application/json"}) as r:
            return json.loads(await r.text())

async def get_klines(symbol):
    data = await fetch_json(
        "https://api.bybit.com/v5/market/kline",
        {"category": "linear", "symbol": symbol, "interval": INTERVAL, "limit": LIMIT}
    )
    candles = data["result"]["list"]
    closes  = [float(c[4]) for c in reversed(candles)]
    highs   = [float(c[2]) for c in reversed(candles)]
    lows    = [float(c[3]) for c in reversed(candles)]
    volumes = [float(c[5]) for c in reversed(candles)]
    return closes, highs, lows, volumes

async def get_funding(symbol):
    data = await fetch_json(
        "https://api.bybit.com/v5/market/funding/history",
        {"category": "linear", "symbol": symbol, "limit": 1}
    )
    return float(data["result"]["list"][0]["fundingRate"]) * 100

def ma(data, period):
    return sum(data[-period:]) / period if len(data) >= period else None

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

async def analyze_symbol(symbol):
    try:
        closes, highs, lows, volumes = await get_klines(symbol)
        funding = await get_funding(symbol)
        price   = closes[-1]
        ma7     = ma(closes, 7)
        ma14    = ma(closes, 14)
        ma28    = ma(closes, 28)
        rsi_v   = rsi(closes)
        avg_vol   = sum(volumes[-10:]) / 10
        vol_surge = volumes[-1] > avg_vol * 1.5
        above_all = price > ma7 > ma14 > ma28
        below_all = price < ma7 < ma14 < ma28

        signal = "⚪ WAIT"
        direction = "TUNGGU"
        confidence = "Rendah"
        sl_pct = 0.5
        tp_pct = 1.0

        if above_all and rsi_v < 70 and funding > -0.01:
            signal, direction = "🟢 LONG", "LONG"
            confidence = "Tinggi" if vol_surge else "Medium"
            tp_pct = 1.5 if vol_surge else 1.0
        elif below_all and rsi_v > 30 and funding < 0.01:
            signal, direction = "🔴 SHORT", "SHORT"
            confidence = "Tinggi" if vol_surge else "Medium"
            tp_pct = 1.5 if vol_surge else 1.0
        elif rsi_v > 75:
            signal, direction, confidence = "🔴 SHORT", "SHORT", "Medium"
        elif rsi_v < 25:
            signal, direction, confidence = "🟢 LONG", "LONG", "Medium"

        if direction in ("LONG", "SHORT"):
            entry = round(price, 4)
            sl = round(price * (1 - sl_pct/100 if direction == "LONG" else 1 + sl_pct/100), 4)
            tp = round(price * (1 + tp_pct/100 if direction == "LONG" else 1 - tp_pct/100), 4)
        else:
            entry = sl = tp = 0

        return {"symbol": symbol, "price": price, "signal": signal,
                "direction": direction, "entry": entry, "sl": sl, "tp": tp,
                "confidence": confidence, "rsi": round(rsi_v, 1),
                "funding": round(funding, 4), "vol_surge": vol_surge}
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}

def format_one(a):
    if "error" in a:
        return f"⚠️ {a['symbol']}: Error — {a['error']}"
    vol = " ⚡" if a["vol_surge"] else ""
    if a["entry"] == 0:
        return (f"{a['signal']} {a['symbol']}\n"
                f"💰 {a['price']} | RSI: {a['rsi']} | Fund: {a['funding']}%\n"
                f"❌ Tidak ada setup jelas\n")
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
                result = await analyze_symbol(symbol)
                results.append(result)
                await asyncio.sleep(1)  # hindari rate limit

            # Urutkan: LONG/SHORT dulu, WAIT belakang
            results.sort(key=lambda x: 0 if x.get("direction","") in ("LONG","SHORT") else 1)

            msg = f"📊 Sinyal {now}\n{'━'*22}\n"
            for r in results:
                msg += format_one(r) + "\n"
            msg += "⚠️ Bukan financial advice. Selalu pakai SL!"

            await bot.send_message(chat_id=CHAT_ID, text=msg)
            print(f"[{datetime.utcnow()}] Sinyal terkirim untuk {len(SYMBOLS)} koin")
        except Exception as e:
            print(f"[ERROR] {e}")
        await asyncio.sleep(SCHEDULE_MIN * 60)

if __name__ == "__main__":
    asyncio.run(main())
