import os
import asyncio
import aiohttp
from datetime import datetime
from telegram import Bot
from telegram.constants import ParseMode

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")
SYMBOL         = "BTCUSDT"
INTERVAL       = "15"
LIMIT          = 50
SCHEDULE_MIN   = 15

async def get_klines():
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category": "linear", "symbol": SYMBOL, "interval": INTERVAL, "limit": LIMIT}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as r:
            data = await r.json()
    candles = data["result"]["list"]
    closes  = [float(c[4]) for c in reversed(candles)]
    highs   = [float(c[2]) for c in reversed(candles)]
    lows    = [float(c[3]) for c in reversed(candles)]
    volumes = [float(c[5]) for c in reversed(candles)]
    return closes, highs, lows, volumes

async def get_funding_rate():
    url = "https://api.bybit.com/v5/market/funding/history"
    params = {"category": "linear", "symbol": SYMBOL, "limit": 1}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as r:
            data = await r.json()
    return float(data["result"]["list"][0]["fundingRate"]) * 100

def ma(data, period):
    if len(data) < period:
        return None
    return sum(data[-period:]) / period

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
        tr = max(highs[-i] - lows[-i], abs(highs[-i] - closes[-i-1]), abs(lows[-i] - closes[-i-1]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0

async def analyze():
    closes, highs, lows, volumes = await get_klines()
    funding = await get_funding_rate()
    price   = closes[-1]
    ma7     = ma(closes, 7)
    ma14    = ma(closes, 14)
    ma28    = ma(closes, 28)
    rsi_v   = rsi(closes)
    atr_v   = atr(highs, lows, closes)
    avg_vol   = sum(volumes[-10:]) / 10
    vol_surge = volumes[-1] > avg_vol * 1.5
    above_all_ma = price > ma7 > ma14 > ma28
    below_all_ma = price < ma7 < ma14 < ma28
    signal = "⚪ WAIT"
    direction = "TUNGGU"
    confidence = "Rendah"
    sl_pct = 0.5
    tp_pct = 1.0
    if above_all_ma and rsi_v < 70 and funding > -0.01:
        signal, direction, confidence = "🟢 LONG", "LONG", "Tinggi" if vol_surge else "Medium"
        tp_pct = 1.5 if vol_surge else 1.0
    elif below_all_ma and rsi_v > 30 and funding < 0.01:
        signal, direction, confidence = "🔴 SHORT", "SHORT", "Tinggi" if vol_surge else "Medium"
        tp_pct = 1.5 if vol_surge else 1.0
    elif rsi_v > 75:
        signal, direction, confidence = "🔴 SHORT", "SHORT (Overbought)", "Medium"
    elif rsi_v < 25:
        signal, direction, confidence = "🟢 LONG", "LONG (Oversold)", "Medium"
    if direction.startswith("LONG"):
        entry = round(price, 1)
        sl    = round(price * (1 - sl_pct / 100), 1)
        tp    = round(price * (1 + tp_pct / 100), 1)
    elif direction.startswith("SHORT"):
        entry = round(price, 1)
        sl    = round(price * (1 + sl_pct / 100), 1)
        tp    = round(price * (1 - tp_pct / 100), 1)
    else:
        entry = sl = tp = 0
    return {"price": price, "signal": signal, "direction": direction, "entry": entry,
            "sl": sl, "tp": tp, "confidence": confidence, "rsi": round(rsi_v, 1),
            "ma7": round(ma7, 1), "ma14": round(ma14, 1), "ma28": round(ma28, 1),
            "funding": round(funding, 4), "vol_surge": vol_surge, "atr": round(atr_v, 1)}

def format_message(a):
    now   = datetime.utcnow().strftime("%H:%M UTC")
    emoji = "🚀" if "LONG" in a["direction"] else ("🔻" if "SHORT" in a["direction"] else "😴")
    vol   = "⚡ Volume Surge!" if a["vol_surge"] else ""
    if a["entry"] == 0:
        trade_info = "❌ Tidak ada setup jelas.\nSabar tunggu konfirmasi."
    else:
        trade_info = (f"📍 *Entry :* `{a['entry']}`\n"
                      f"🎯 *TP    :* `{a['tp']}` (+{round((abs(a['tp']-a['entry'])/a['entry'])*100,2)}%)\n"
                      f"🛑 *SL    :* `{a['sl']}` (-{round((abs(a['sl']-a['entry'])/a['entry'])*100,2)}%)\n")
    return f"""
{emoji} *BTCUSDT Signal — {now}*
━━━━━━━━━━━━━━━━━━
💰 *Harga  :* `{a['price']}`
📊 *Sinyal :* {a['signal']}
🎖 *Confidence :* {a['confidence']} {vol}

{trade_info}
━━━━━━━━━━━━━━━━━━
📈 *Indikator*
• RSI    : `{a['rsi']}` {'🔥 OB' if a['rsi']>70 else ('❄️ OS' if a['rsi']<30 else '✅')}
• MA7    : `{a['ma7']}`
• MA14   : `{a['ma14']}`
• MA28   : `{a['ma28']}`
• Funding: `{a['funding']}%`

⚠️ _Bukan financial advice. Selalu pakai SL!_
""".strip()

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    print(f"✅ Bot started — sinyal tiap {SCHEDULE_MIN} menit")
    while True:
        try:
            analysis = await analyze()
            await bot.send_message(chat_id=CHAT_ID, text=format_message(analysis), parse_mode=ParseMode.MARKDOWN)
            print(f"[{datetime.utcnow()}] Sinyal terkirim: {analysis['signal']}")
        except Exception as e:
            print(f"[ERROR] {e}")
        await asyncio.sleep(SCHEDULE_MIN * 60)

if __name__ == "__main__":
    asyncio.run(main())
