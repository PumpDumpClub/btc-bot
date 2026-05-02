import os
import asyncio
import aiohttp
from datetime import datetime, timezone
from telegram import Bot

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")
SCHEDULE_MIN   = 15
INTERVAL       = "15"
LIMIT          = 100

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT"
]

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

def atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, min(period + 1, len(closes))):
        tr = max(highs[-i] - lows[-i],
                 abs(highs[-i] - closes[-i-1]),
                 abs(lows[-i]  - closes[-i-1]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0

def ema(data, period):
    if len(data) < period:
        return 0
    k = 2 / (period + 1)
    val = sum(data[:period]) / period
    for price in data[period:]:
        val = price * k + val * (1 - k)
    return val

def macd(closes):
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    return ema12 - ema26

def volume_trend(volumes):
    # Volume 3 candle terakhir vs 10 candle sebelumnya
    recent  = sum(volumes[-3:]) / 3
    average = sum(volumes[-13:-3]) / 10
    return recent / average if average > 0 else 1

def candle_strength(closes, highs, lows):
    # Body candle terakhir vs shadow — makin besar makin kuat
    body   = abs(closes[-1] - closes[-2])
    shadow = highs[-1] - lows[-1]
    return body / shadow if shadow > 0 else 0

async def analyze(symbol):
    closes, highs, lows, volumes = await get_klines(symbol)
    if closes is None:
        return None
    funding  = await get_funding(symbol)
    price    = closes[-1]

    # Indikator
    ma7      = ma(closes, 7)
    ma14     = ma(closes, 14)
    ma25     = ma(closes, 25)
    ma99     = ma(closes, 99)
    rsi_v    = rsi(closes)
    atr_v    = atr(highs, lows, closes)
    macd_v   = macd(closes)
    vol_ratio = volume_trend(volumes)
    candle_s  = candle_strength(closes, highs, lows)

    # ── FILTER KETAT ─────────────────────────────────────
    # Semua syarat HARUS terpenuhi untuk sinyal valid

    score = 0
    direction = None

    # === LONG CONDITIONS ===
    long_conditions = [
        price > ma7 > ma14 > ma25 > ma99,   # Semua MA aligned bullish
        rsi_v < 30,                           # RSI oversold ekstrem
        macd_v > 0,                           # MACD positif
        vol_ratio > 1.8,                      # Volume 80% di atas rata-rata
        candle_s > 0.6,                       # Candle body kuat
        funding < 0.005,                      # Funding tidak terlalu positif
        closes[-1] > closes[-2] > closes[-3], # 3 candle naik berturut
    ]

    # === SHORT CONDITIONS ===
    short_conditions = [
        price < ma7 < ma14 < ma25 < ma99,   # Semua MA aligned bearish
        rsi_v > 72,                           # RSI overbought ekstrem
        macd_v < 0,                           # MACD negatif
        vol_ratio > 1.8,                      # Volume 80% di atas rata-rata
        candle_s > 0.6,                       # Candle body kuat
        funding > -0.005,                     # Funding tidak terlalu negatif
        closes[-1] < closes[-2] < closes[-3], # 3 candle turun berturut
    ]

    long_score  = sum(long_conditions)
    short_score = sum(short_conditions)

    # Minimum 6 dari 7 syarat harus terpenuhi
    if long_score >= 6:
        direction = "LONG"
        score = long_score
    elif short_score >= 6:
        direction = "SHORT"
        score = short_score
    else:
        return None  # Tidak lolos filter → tidak dikirim

    # Hitung SL/TP berbasis ATR (lebih presisi dari persentase)
    atr_multiplier_sl = 1.5
    atr_multiplier_tp = 3.0  # RR 1:2

    if direction == "LONG":
        entry  = round(price, 6)
        sl     = round(price - atr_v * atr_multiplier_sl, 6)
        tp     = round(price + atr_v * atr_multiplier_tp, 6)
        sl_pct = round((price - sl) / price * 100, 2)
        tp_pct = round((tp - price) / price * 100, 2)
    else:
        entry  = round(price, 6)
        sl     = round(price + atr_v * atr_multiplier_sl, 6)
        tp     = round(price - atr_v * atr_multiplier_tp, 6)
        sl_pct = round((sl - price) / price * 100, 2)
        tp_pct = round((price - tp) / price * 100, 2)

    # Estimasi berlaku berapa candle (timeframe 15m)
    # ATR based — makin volatile makin cepat TP/SL kena
    candles_estimate = round(atr_multiplier_tp / (atr_v / price * 100) * 0.5)
    candles_estimate = max(2, min(candles_estimate, 12))
    waktu_berlaku    = candles_estimate * 15  # dalam menit

    return {
        "symbol"   : symbol,
        "price"    : price,
        "direction": direction,
        "entry"    : entry,
        "sl"       : sl,
        "tp"       : tp,
        "sl_pct"   : sl_pct,
        "tp_pct"   : tp_pct,
        "rsi"      : round(rsi_v, 1),
        "funding"  : round(funding, 4),
        "vol_ratio": round(vol_ratio, 1),
        "score"    : score,
        "waktu"    : waktu_berlaku,
        "atr"      : round(atr_v, 6),
    }

def format_signal(a):
    emoji  = "🚀" if a["direction"] == "LONG" else "🔻"
    bintang = "⭐" * (a["score"] - 5)  # 1-2 bintang
    jam    = a["waktu"] // 60
    menit  = a["waktu"] % 60
    durasi = f"{jam}j {menit}m" if jam > 0 else f"{menit} menit"

    return (
        f"{emoji} {a['direction']} {a['symbol']} {bintang}\n"
        f"{'━'*24}\n"
        f"💰 Harga   : {a['price']}\n"
        f"📍 Entry   : {a['entry']}\n"
        f"🎯 TP      : {a['tp']} (+{a['tp_pct']}%)\n"
        f"🛑 SL      : {a['sl']} (-{a['sl_pct']}%)\n"
        f"⏱ Berlaku : ±{durasi}\n"
        f"{'━'*24}\n"
        f"📊 RSI     : {a['rsi']}\n"
        f"📈 Volume  : {a['vol_ratio']}x rata-rata\n"
        f"💸 Funding : {a['funding']}%\n"
        f"🎯 Score   : {a['score']}/7 syarat terpenuhi\n"
    )

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    print(f"✅ Bot started — filter ketat, {len(SYMBOLS)} koin, tiap {SCHEDULE_MIN} menit")

    while True:
        try:
            now     = datetime.now(timezone.utc).strftime("%H:%M UTC")
            signals = []

            for symbol in SYMBOLS:
                result = await analyze(symbol)
                if result:
                    signals.append(result)
                await asyncio.sleep(2)

            if signals:
                # Urutkan by score tertinggi
                signals.sort(key=lambda x: x["score"], reverse=True)
                msg = f"🔥 SINYAL KUAT — {now}\n\n"
                for s in signals:
                    msg += format_signal(s) + "\n"
                msg += "⚠️ Bukan financial advice. Selalu pakai SL!"
            else:
                msg = (f"😴 {now}\n"
                       f"Belum ada sinyal kuat bosku ini.\n"
                       f"Tetap kita pantau ya ...\n"
                       f"Cuan cuan cuan sempurna! 🎯")

            await bot.send_message(chat_id=CHAT_ID, text=msg)
            print(f"[OK] {now} — {len(signals)} sinyal terkirim")

        except Exception as e:
            print(f"[ERROR] {e}")

        await asyncio.sleep(SCHEDULE_MIN * 60)

if __name__ == "__main__":
    asyncio.run(main())
