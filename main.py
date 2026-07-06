"""
Tilla (XAUUSD) Wyckoff Spring/Upthrust signal beruvchi va soatlik holat
xabar qiluvchi Telegram bot.

Ikki rejimda ishlaydi (Render'da ikkita alohida Cron Job sifatida sozlanadi):

  python main.py signal   -> har 5 daqiqada: faqat SPRING/UPTHRUST chiqqanda
                              to'liq signal + grafik + AI tahlil yuboradi.
                              Signal bo'lmasa, jim chiqadi (xabar yubormaydi).

  python main.py status   -> har soatda: joriy narx, diapazon va
                              range/uchburchak holatini qisqa xabar qilib yuboradi.
"""

import os
import sys
import requests

# ---------- Sozlamalar (Render'da Environment Variables sifatida kiritiladi) ----------
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY")
NEWSAPI_API_KEY = os.environ.get("NEWSAPI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

REQUIRED_VARS = {
    "TWELVEDATA_API_KEY": TWELVEDATA_API_KEY,
    "NEWSAPI_API_KEY": NEWSAPI_API_KEY,
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
}

RANGE_LOOKBACK = 20      # diapazonni aniqlash uchun necha sveчadan foydalanish
VOLUME_MULTIPLIER = 1.5  # hajm "tasdiq" deb hisoblanishi uchun o'rtachadan necha baravar yuqori bo'lishi kerak


def check_env_vars():
    missing = [name for name, value in REQUIRED_VARS.items() if not value]
    if missing:
        print(f"XATOLIK: quyidagi environment variable'lar topilmadi: {', '.join(missing)}")
        sys.exit(1)


# ============================================================================
# MA'LUMOT OLISH (TwelveData)
# ============================================================================

def get_gold_price():
    """TwelveData orqali XAU/USD narxi va o'zgarish foizini oladi."""
    url = "https://api.twelvedata.com/quote"
    params = {"symbol": "XAU/USD", "apikey": TWELVEDATA_API_KEY}
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if "code" in data and data.get("code") != 200:
        raise RuntimeError(f"TwelveData xatosi: {data.get('message')}")

    return {
        "price": data.get("close"),
        "change": data.get("change"),
        "percent_change": data.get("percent_change"),
        "high": data.get("high"),
        "low": data.get("low"),
        "volume": data.get("volume"),
    }


def get_gold_candles(interval="5min", outputsize=100):
    """TwelveData'dan oxirgi svechalar tarixini (OHLCV) oladi."""
    import pandas as pd

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": "XAU/USD",
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if "values" not in data:
        raise RuntimeError(f"TwelveData time_series xatosi: {data.get('message', data)}")

    df = pd.DataFrame(data["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
        else:
            df[col] = 0.0

    return df


def get_gold_news():
    """NewsAPI orqali oltin/XAUUSD'ga aloqador so'nggi yangiliklarni oladi."""
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": "gold price OR XAUUSD OR bullion",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 5,
        "apiKey": NEWSAPI_API_KEY,
    }
    resp = requests.get(url, params=params, timeout=15)
    data = resp.json()

    if data.get("status") == "error":
        raise RuntimeError(f"NewsAPI xatosi ({data.get('code')}): {data.get('message')}")
    resp.raise_for_status()

    total_results = data.get("totalResults", 0)
    articles = data.get("articles", [])
    headlines = []
    for a in articles[:5]:
        title = a.get("title", "")
        source = a.get("source", {}).get("name", "")
        if title:
            headlines.append(f"- {title} ({source})")

    return headlines, total_results


# ============================================================================
# QOIDA DVIGATELI - Wyckoff Spring / Upthrust / Range holati
# ============================================================================

def detect_spring(df, lookback=RANGE_LOOKBACK, vol_mult=VOLUME_MULTIPLIER):
    """SPRING: narx diapazon pastki chegarasidan soxta chiqib, qaytib kiradi,
    va bu hajm bilan tasdiqlanadi (o'rtachadan yuqori hajm)."""
    if len(df) < lookback + 1:
        return None

    window = df.iloc[-(lookback + 1):-1]
    current = df.iloc[-1]

    range_low = window["low"].min()
    range_high = window["high"].max()
    avg_volume = window["volume"].mean()

    is_false_breakdown = current["low"] < range_low and current["close"] > range_low
    is_volume_confirmed = avg_volume > 0 and current["volume"] > avg_volume * vol_mult

    if is_false_breakdown and is_volume_confirmed:
        return {
            "type": "spring",
            "range_low": range_low,
            "range_high": range_high,
            "candle_low": current["low"],
            "candle_close": current["close"],
            "volume": current["volume"],
            "avg_volume": avg_volume,
            "time": str(current.name),
        }
    return None


def detect_upthrust(df, lookback=RANGE_LOOKBACK, vol_mult=VOLUME_MULTIPLIER):
    """UPTHRUST: narx diapazon yuqori chegarasidan soxta chiqib, qaytib kiradi,
    va bu hajm bilan tasdiqlanadi."""
    if len(df) < lookback + 1:
        return None

    window = df.iloc[-(lookback + 1):-1]
    current = df.iloc[-1]

    range_low = window["low"].min()
    range_high = window["high"].max()
    avg_volume = window["volume"].mean()

    is_false_breakout = current["high"] > range_high and current["close"] < range_high
    is_volume_confirmed = avg_volume > 0 and current["volume"] > avg_volume * vol_mult

    if is_false_breakout and is_volume_confirmed:
        return {
            "type": "upthrust",
            "range_low": range_low,
            "range_high": range_high,
            "candle_high": current["high"],
            "candle_close": current["close"],
            "volume": current["volume"],
            "avg_volume": avg_volume,
            "time": str(current.name),
        }
    return None


def detect_range_state(df, lookback=RANGE_LOOKBACK, tight_threshold_pct=0.5):
    """Joriy holat qanday diapazon/uchburchak turiga to'g'ri kelishini aniqlaydi:
    - bullish_squeeze: pastki chegara ko'tarilib, yuqoriga qisilmoqda
    - bearish_squeeze: yuqori chegara pasayib, pastga qisilmoqda
    - symmetrical_triangle: ikkala tomondan torayapti
    - flat_range: torayish yo'q, lekin narx tor oraliqda (oddiy gorizontal diapazon)
    - None: aniq diapazon yo'q (narx keng harakatda / trendda)
    """
    if len(df) < lookback:
        return None

    window = df.iloc[-lookback:]
    half = lookback // 2
    first_half = window.iloc[:half]
    second_half = window.iloc[half:]

    high_first, high_second = first_half["high"].max(), second_half["high"].max()
    low_first, low_second = first_half["low"].min(), second_half["low"].min()

    width_first = high_first - low_first
    width_second = high_second - low_second
    current_price = window["close"].iloc[-1]

    if current_price <= 0:
        return None

    width_pct = (width_second / current_price) * 100

    if width_pct > tight_threshold_pct * 3:
        return None

    narrowing = width_second < width_first * 0.75
    high_falling = high_second < high_first
    low_rising = low_second > low_first

    if narrowing and low_rising and not high_falling:
        rtype = "bullish_squeeze"
    elif narrowing and high_falling and not low_rising:
        rtype = "bearish_squeeze"
    elif narrowing and low_rising and high_falling:
        rtype = "symmetrical_triangle"
    else:
        rtype = "flat_range"

    return {
        "type": rtype,
        "range_high": high_second,
        "range_low": low_second,
        "width_pct": round(width_pct, 3),
    }


# ============================================================================
# GRAFIK CHIZISH
# ============================================================================

def make_chart_image(df, path="/tmp/chart.png"):
    """OHLCV ma'lumotidan katta, aniq o'qiladigan candlestick + volume grafik chizadi."""
    import mplfinance as mpf

    mc = mpf.make_marketcolors(
        up="#26a69a", down="#ef5350",
        edge="inherit", wick="inherit", volume="in",
    )
    style = mpf.make_mpf_style(
        base_mpf_style="charles",
        marketcolors=mc,
        gridstyle="--",
        gridcolor="#dddddd",
        facecolor="white",
        rc={"font.size": 11, "axes.labelsize": 12, "axes.titlesize": 14},
    )

    mpf.plot(
        df,
        type="candle",
        volume=True,
        style=style,
        title="\nXAUUSD - so'nggi 100 ta 5 daqiqalik sveча",
        ylabel="Narx (USD)",
        ylabel_lower="Hajm",
        figsize=(16, 9),
        tight_layout=True,
        scale_padding={"left": 0.3, "right": 0.7, "top": 0.8, "bottom": 0.5},
        savefig=dict(fname=path, dpi=220, bbox_inches="tight"),
    )
    return path


# ============================================================================
# AI TAHLIL (faqat signal chiqqanda chaqiriladi)
# ============================================================================

def analyze_with_claude(chart_path, price_data, headlines, signal):
    """Grafik, narx, aniqlangan signal va yangiliklarni Claude API'ga yuborib,
    signalni tasdiqlovchi/rad etuvchi qisqa tahlil oldiradi."""
    import base64

    news_text = "\n".join(headlines) if headlines else "Yangilik topilmadi."

    with open(chart_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    if signal["type"] == "spring":
        signal_desc = (
            f"SPRING (Wyckoff) — narx {signal['range_low']:.2f} diapazon pastki chegarasidan "
            f"soxta chiqib ({signal['candle_low']:.2f} gacha tushib), qaytib {signal['candle_close']:.2f} "
            f"darajasida yopilgan. Hajm {signal['volume']:.0f}, o'rtacha hajmdan "
            f"({signal['avg_volume']:.0f}) sezilarli yuqori."
        )
    else:
        signal_desc = (
            f"UPTHRUST (Wyckoff) — narx {signal['range_high']:.2f} diapazon yuqori chegarasidan "
            f"soxta chiqib ({signal['candle_high']:.2f} gacha ko'tarilib), qaytib {signal['candle_close']:.2f} "
            f"darajasida yopilgan. Hajm {signal['volume']:.0f}, o'rtacha hajmdan "
            f"({signal['avg_volume']:.0f}) sezilarli yuqori."
        )

    prompt = f"""Sen SMC/ICT/Wyckoff va hajm tahliliga ixtisoslashgan treyder-tahlilchisan.

Kod avtomatik ravishda quyidagi signalni aniqladi:
{signal_desc}

Ilova qilingan XAUUSD grafigini ko'rib:
1. Shu signalni TASDIQLA yoki unga SHUBHA bildir (nima uchun ishonchli/ishonchsiz)
2. Liquidity va order block nuqtai nazaridan qo'shimcha kontekst ber
3. Quyida berilgan so'nggi yangiliklarni sharhla — ular ushbu signalga mos keladimi
   yoki qarama-qarshimi (masalan signal bullish, lekin yangilik bearish bo'lsa, buni ayt)

Javobni o'zbek tilida, Telegram xabari uchun mos, qisqa va aniq formatda yoz.
Bashorat yoki "sotib ol/sot" tavsiyasi berma — faqat texnik kuzatuv va yangilik konteksti ber.
Oxirida "Bu tavsiya emas, faqat texnik kuzatuv" deb yoz.

Qo'shimcha ma'lumot:
Joriy narx: {price_data['price']}
O'zgarish: {price_data['change']} ({price_data['percent_change']}%)

So'nggi yangiliklar (sarlavhalar):
{news_text}
"""

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 1000,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": image_b64},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(text_blocks).strip()


# ============================================================================
# TELEGRAM YUBORISH
# ============================================================================

def send_telegram_message(text):
    """Matnli xabar yuboradi. Uzun bo'lsa avtomatik bo'laklarga bo'ladi."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    max_len = 4000
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [text]
    for chunk in chunks:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk}, timeout=15)
        resp.raise_for_status()


def send_telegram_document(file_path, caption=""):
    """Grafikni HUJJAT sifatida (siqilmasdan, yuqori sifatda) yuboradi."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    with open(file_path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1000]},
            files={"document": f},
            timeout=30,
        )
    resp.raise_for_status()


# ============================================================================
# REJIM 1: SIGNAL TEKSHIRUV (har 5 daqiqada)
# ============================================================================

def run_signal_check(df, price_data):
    spring = detect_spring(df)
    upthrust = detect_upthrust(df)
    signal = spring or upthrust

    if not signal:
        print("Signal yo'q — jim chiqamiz.")
        return

    chart_path = make_chart_image(df)

    news_error = None
    total_results = None
    try:
        headlines, total_results = get_gold_news()
    except Exception as e:
        news_error = str(e)
        headlines = []

    try:
        analysis = analyze_with_claude(chart_path, price_data, headlines, signal)
    except Exception as e:
        analysis = f"(AI tahlili olinmadi: {e})"

    if signal["type"] == "spring":
        emoji, label = "🟢", "SPRING (pastga soxta sinish -> mumkin bo'lgan ko'tarilish)"
    else:
        emoji, label = "🔴", "UPTHRUST (yuqoriga soxta sinish -> mumkin bo'lgan tushish)"

    caption = (
        f"{emoji} {label}\n"
        f"Narx: {price_data['price']} USD\n"
        f"Diapazon: {signal['range_low']:.2f} - {signal['range_high']:.2f}\n"
        f"Hajm: {signal['volume']:.0f} (o'rtacha: {signal['avg_volume']:.0f})"
    )
    send_telegram_document(chart_path, caption=caption)

    full_analysis = f"📊 AI Tahlili:\n\n{analysis}"
    if news_error:
        full_analysis += f"\n\n⚠️ Yangilik olinmadi (xatolik): {news_error}"
    elif not headlines:
        full_analysis += f"\n\nℹ️ Yangilik topilmadi (NewsAPI natijasi: {total_results} ta maqola)."
    else:
        full_analysis += f"\n\nℹ️ Tahlilda ishlatilgan yangiliklar soni: {len(headlines)}"

    send_telegram_message(full_analysis)
    print(f"{label} signali yuborildi.")


# ============================================================================
# REJIM 2: SOATLIK HOLAT (har soatda)
# ============================================================================

RANGE_TYPE_NAMES = {
    "bullish_squeeze": "📈 Yuqoriga qisilish (bullish squeeze) — pastki chegara ko'tarilmoqda",
    "bearish_squeeze": "📉 Pastga qisilish (bearish squeeze) — yuqori chegara pasaymoqda",
    "symmetrical_triangle": "🔺 Simmetrik uchburchak — ikkala tomondan torayapti",
    "flat_range": "📦 Oddiy gorizontal diapazon — tor oraliqda tebranmoqda",
}


def run_hourly_status(df, price_data):
    lookback = RANGE_LOOKBACK
    if len(df) < lookback + 1:
        send_telegram_message("🕐 Soatlik holat: ma'lumot yetarli emas.")
        return

    window = df.iloc[-(lookback + 1):-1]
    range_low = window["low"].min()
    range_high = window["high"].max()

    range_state = detect_range_state(df, lookback=lookback)

    lines = [
        "🕐 Soatlik holat",
        f"Narx: {price_data['price']} USD",
        f"Diapazon (so'nggi {lookback} sveча): {range_low:.2f} – {range_high:.2f}",
    ]

    if range_state:
        lines.append(f"\n{RANGE_TYPE_NAMES.get(range_state['type'], range_state['type'])}")
    else:
        lines.append("\n📐 Holat: Aniq diapazon/uchburchak shakli yo'q (trend/keng harakat)")

    lines.append("\nSignal: Hozircha spring/upthrust aniqlanmadi (aniqlansa alohida xabar keladi)")

    send_telegram_message("\n".join(lines))
    print("Soatlik holat yuborildi.")


# ============================================================================
# ASOSIY DASTUR
# ============================================================================

def main():
    check_env_vars()
    mode = sys.argv[1] if len(sys.argv) > 1 else "signal"

    try:
        price_data = get_gold_price()
    except Exception as e:
        send_telegram_message(f"⚠️ Narx ma'lumotini olishda xatolik: {e}")
        sys.exit(1)

    try:
        candles_df = get_gold_candles(interval="5min", outputsize=100)
    except Exception as e:
        send_telegram_message(f"⚠️ Sveча ma'lumotini olishda xatolik: {e}")
        sys.exit(1)

    if mode == "signal":
        run_signal_check(candles_df, price_data)
    elif mode == "status":
        run_hourly_status(candles_df, price_data)
    else:
        print(f"Noma'lum rejim: {mode}. 'signal' yoki 'status' bo'lishi kerak.")
        sys.exit(1)


if __name__ == "__main__":
    main()
