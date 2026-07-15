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
EFFORT_MULTIPLIER = 1.5  # svecha "kuchli harakat" deb hisoblanishi uchun o'rtacha svecha
                         # kattaligidan necha baravar yuqori bo'lishi kerak
                         # (XAUUSD'da haqiqiy savdo hajmi mavjud emasligi sababli,
                         # hajm o'rniga svecha kattaligi - high-low farqi - ishlatiladi)


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


def get_forex_calendar_events(hours_ahead=24):
    """Forex Factory'ning ochiq JSON kalendaridan yaqin soatlardagi yuqori ta'sirli
    USD iqtisodiy yangiliklarini oladi (Fed, NFP, CPI kabi — bular XAUUSD'ga eng
    ko'p ta'sir qiladigan voqealar). Diqqat: bu manzilga 5 daqiqada faqat 2 marta
    so'rov yuborish mumkin — shuning uchun faqat soatlik status rejimida chaqiriladi."""
    import datetime as dt
    from dateutil import parser as date_parser

    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()

    text = resp.text.strip()
    if text.startswith("<") or "Request Denied" in text:
        raise RuntimeError("Forex Factory limitga tegib qoldi (5 daqiqada 2 so'rovdan ko'p)")

    events = resp.json()
    now = dt.datetime.now(dt.timezone.utc)
    horizon = now + dt.timedelta(hours=hours_ahead)

    result = []
    for e in events:
        try:
            event_time = date_parser.parse(e.get("date", ""))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=dt.timezone.utc)
        except (ValueError, TypeError):
            continue

        if e.get("country") == "USD" and e.get("impact") == "High" and now <= event_time <= horizon:
            result.append({
                "title": e.get("title", "Noma'lum voqea"),
                "time": event_time,
                "forecast": e.get("forecast", ""),
                "previous": e.get("previous", ""),
            })

    result.sort(key=lambda x: x["time"])
    return result


# ============================================================================
# QOIDA DVIGATELI - Wyckoff Spring / Upthrust / Range holati
# ============================================================================

def detect_spring(df, lookback=RANGE_LOOKBACK, effort_mult=EFFORT_MULTIPLIER):
    """SPRING: narx diapazon pastki chegarasidan soxta chiqib, qaytib kiradi,
    va bu KUCHLI HARAKAT (o'rtachadan kattaroq svecha) bilan tasdiqlanadi.
    (XAUUSD'da haqiqiy hajm yo'qligi sababli, svecha kattaligi - high-low - ishlatiladi)."""
    if len(df) < lookback + 1:
        return None

    window = df.iloc[-(lookback + 1):-1]
    current = df.iloc[-1]

    range_low = window["low"].min()
    range_high = window["high"].max()
    avg_candle_range = (window["high"] - window["low"]).mean()
    current_candle_range = current["high"] - current["low"]

    is_false_breakdown = current["low"] < range_low and current["close"] > range_low
    is_effort_confirmed = avg_candle_range > 0 and current_candle_range > avg_candle_range * effort_mult

    if is_false_breakdown and is_effort_confirmed:
        return {
            "type": "spring",
            "range_low": range_low,
            "range_high": range_high,
            "candle_low": current["low"],
            "candle_close": current["close"],
            "candle_range": current_candle_range,
            "avg_candle_range": avg_candle_range,
            "time": str(current.name),
        }
    return None


def detect_upthrust(df, lookback=RANGE_LOOKBACK, effort_mult=EFFORT_MULTIPLIER):
    """UPTHRUST: narx diapazon yuqori chegarasidan soxta chiqib, qaytib kiradi,
    va bu KUCHLI HARAKAT (o'rtachadan kattaroq svecha) bilan tasdiqlanadi."""
    if len(df) < lookback + 1:
        return None

    window = df.iloc[-(lookback + 1):-1]
    current = df.iloc[-1]

    range_low = window["low"].min()
    range_high = window["high"].max()
    avg_candle_range = (window["high"] - window["low"]).mean()
    current_candle_range = current["high"] - current["low"]

    is_false_breakout = current["high"] > range_high and current["close"] < range_high
    is_effort_confirmed = avg_candle_range > 0 and current_candle_range > avg_candle_range * effort_mult

    if is_false_breakout and is_effort_confirmed:
        return {
            "type": "upthrust",
            "range_low": range_low,
            "range_high": range_high,
            "candle_high": current["high"],
            "candle_close": current["close"],
            "candle_range": current_candle_range,
            "avg_candle_range": avg_candle_range,
            "time": str(current.name),
        }
    return None


def find_swing_points(highs, lows, window=3, exclude_last=True):
    """Har bir nuqta atrofida (window ta oldin, window ta keyin) eng yuqori/past
    bo'lsa, uni tasdiqlangan swing high/low deb belgilaydi."""
    n = len(highs)
    swing_high_idx, swing_low_idx = [], []
    end = n - 1 if exclude_last else n
    for i in range(window, end):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)
        if highs[i] == highs[lo:hi].max():
            swing_high_idx.append(i)
        if lows[i] == lows[lo:hi].min():
            swing_low_idx.append(i)
    return swing_high_idx, swing_low_idx


def detect_smc_composite(df, swing_window=3, lookback=40):
    """ENG KUCHLI SMC/ICT signal: Liquidity Sweep + FVG + BOS/CHoCH ketma-ketligi.
    Faqat BOS/CHoCH aynan JORIY (oxirgi) svechada tasdiqlansa signal beradi —
    shu bilan har bir voqea faqat bir marta xabar qilinadi."""
    if len(df) < lookback:
        return None

    sub = df.iloc[-lookback:]
    highs = sub["high"].values
    lows = sub["low"].values
    closes = sub["close"].values
    times = sub.index
    n = len(sub)
    cur = n - 1

    swing_high_idx, swing_low_idx = find_swing_points(highs, lows, window=swing_window, exclude_last=True)
    if not swing_high_idx or not swing_low_idx:
        return None

    prior_highs_before = lambda idx: [i for i in swing_high_idx if i < idx]
    prior_lows_before = lambda idx: [i for i in swing_low_idx if i < idx]

    # --- BULLISH: sell-side sweep -> bullish FVG -> BOS yuqoriga (joriy svechada) ---
    ph = prior_highs_before(cur)
    if ph:
        last_swing_high = highs[ph[-1]]
        is_fresh_break = closes[cur] > last_swing_high and closes[cur - 1] <= last_swing_high
        if is_fresh_break:
            # FVG qidiramiz (joriy svechadan oldin): lows[j] > highs[j-2]
            fvg_idx = None
            for j in range(swing_window, cur):
                if j >= 2 and lows[j] > highs[j - 2]:
                    fvg_idx = j
            if fvg_idx is not None:
                # Sweep qidiramiz (FVG'dan oldin): past nuqta swing low'dan pastga tushib, qaytgan
                for k in range(swing_window, fvg_idx):
                    pl = prior_lows_before(k)
                    if pl:
                        sl = lows[pl[-1]]
                        if lows[k] < sl and closes[k] > sl:
                            return {
                                "type": "smc_bullish",
                                "sweep_time": str(times[k]),
                                "sweep_level": sl,
                                "fvg_time": str(times[fvg_idx]),
                                "bos_level": last_swing_high,
                                "current_close": closes[cur],
                            }

    # --- BEARISH: buy-side sweep -> bearish FVG -> BOS pastga (joriy svechada) ---
    pl2 = prior_lows_before(cur)
    if pl2:
        last_swing_low = lows[pl2[-1]]
        is_fresh_break = closes[cur] < last_swing_low and closes[cur - 1] >= last_swing_low
        if is_fresh_break:
            fvg_idx = None
            for j in range(swing_window, cur):
                if j >= 2 and highs[j] < lows[j - 2]:
                    fvg_idx = j
            if fvg_idx is not None:
                for k in range(swing_window, fvg_idx):
                    ph2 = prior_highs_before(k)
                    if ph2:
                        sh = highs[ph2[-1]]
                        if highs[k] > sh and closes[k] < sh:
                            return {
                                "type": "smc_bearish",
                                "sweep_time": str(times[k]),
                                "sweep_level": sh,
                                "fvg_time": str(times[fvg_idx]),
                                "bos_level": last_swing_low,
                                "current_close": closes[cur],
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

def make_chart_image(df, path="/tmp/chart.png", interval="5min"):
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
        volume=False,
        style=style,
        title=f"\nXAUUSD - so'nggi {len(df)} ta {interval} sveча",
        ylabel="Narx (USD)",
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

    if signal["type"] == "smc_bullish":
        signal_desc = (
            f"KUCHLI SIGNAL — Liquidity Sweep + FVG + BOS (BULLISH): "
            f"narx avval {signal['sweep_level']:.2f} darajasidagi (sotuvchilar likvidligi) "
            f"nuqtani sweep qilgan, so'ngra Fair Value Gap (bo'shliq) hosil bo'lgan, "
            f"va yakunda {signal['bos_level']:.2f} darajasini yuqoriga sindirib (BOS), "
            f"{signal['current_close']:.2f} darajasida yopilgan. Bu uchta ICT konsepti "
            f"(sweep + FVG + BOS) ketma-ket bajarilgani — yuqori ishonchli bullish signal deb hisoblanadi."
        )
    elif signal["type"] == "smc_bearish":
        signal_desc = (
            f"KUCHLI SIGNAL — Liquidity Sweep + FVG + BOS (BEARISH): "
            f"narx avval {signal['sweep_level']:.2f} darajasidagi (xaridorlar likvidligi) "
            f"nuqtani sweep qilgan, so'ngra Fair Value Gap (bo'shliq) hosil bo'lgan, "
            f"va yakunda {signal['bos_level']:.2f} darajasini pastga sindirib (BOS), "
            f"{signal['current_close']:.2f} darajasida yopilgan. Bu uchta ICT konsepti "
            f"(sweep + FVG + BOS) ketma-ket bajarilgani — yuqori ishonchli bearish signal deb hisoblanadi."
        )
    elif signal["type"] == "spring":
        signal_desc = (
            f"SPRING (Wyckoff) — narx {signal['range_low']:.2f} diapazon pastki chegarasidan "
            f"soxta chiqib ({signal['candle_low']:.2f} gacha tushib), qaytib {signal['candle_close']:.2f} "
            f"darajasida yopilgan. Svecha kattaligi (high-low) {signal['candle_range']:.2f}, "
            f"o'rtacha svecha kattaligidan ({signal['avg_candle_range']:.2f}) sezilarli katta — "
            f"bu kuchli, keskin harakatni bildiradi (XAUUSD'da haqiqiy savdo hajmi mavjud emasligi "
            f"sababli, svecha kattaligi 'effort' o'lchovi sifatida ishlatiladi)."
        )
    else:
        signal_desc = (
            f"UPTHRUST (Wyckoff) — narx {signal['range_high']:.2f} diapazon yuqori chegarasidan "
            f"soxta chiqib ({signal['candle_high']:.2f} gacha ko'tarilib), qaytib {signal['candle_close']:.2f} "
            f"darajasida yopilgan. Svecha kattaligi (high-low) {signal['candle_range']:.2f}, "
            f"o'rtacha svecha kattaligidan ({signal['avg_candle_range']:.2f}) sezilarli katta — "
            f"bu kuchli, keskin harakatni bildiradi (XAUUSD'da haqiqiy savdo hajmi mavjud emasligi "
            f"sababli, svecha kattaligi 'effort' o'lchovi sifatida ishlatiladi)."
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
            "model": "claude-sonnet-5",
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

def run_signal_check(df, price_data, interval="5min"):
    # Eng kuchli signal birinchi tekshiriladi — agar u chiqsa, boshqalar tekshirilmaydi
    smc = detect_smc_composite(df, lookback=144)
    spring = None if smc else detect_spring(df)
    upthrust = None if (smc or spring) else detect_upthrust(df)
    signal = smc or spring or upthrust

    if not signal:
        print(f"[{interval}] Signal yo'q — jim chiqamiz.")
        return

    chart_path = make_chart_image(df.tail(100), interval=interval)

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

    tf_tag = f"[{interval}]"

    if signal["type"] == "smc_bullish":
        emoji, label = "🔥🟢", f"{tf_tag} KUCHLI SIGNAL: Liquidity Sweep + FVG + BOS (BULLISH)"
        caption = (
            f"{emoji} {label}\n"
            f"Narx: {price_data['price']} USD\n"
            f"Sweep darajasi: {signal['sweep_level']:.2f}\n"
            f"BOS darajasi: {signal['bos_level']:.2f} (yopilish: {signal['current_close']:.2f})"
        )
    elif signal["type"] == "smc_bearish":
        emoji, label = "🔥🔴", f"{tf_tag} KUCHLI SIGNAL: Liquidity Sweep + FVG + BOS (BEARISH)"
        caption = (
            f"{emoji} {label}\n"
            f"Narx: {price_data['price']} USD\n"
            f"Sweep darajasi: {signal['sweep_level']:.2f}\n"
            f"BOS darajasi: {signal['bos_level']:.2f} (yopilish: {signal['current_close']:.2f})"
        )
    elif signal["type"] == "spring":
        emoji, label = "🟢", f"{tf_tag} SPRING (pastga soxta sinish -> mumkin bo'lgan ko'tarilish)"
        caption = (
            f"{emoji} {label}\n"
            f"Narx: {price_data['price']} USD\n"
            f"Diapazon: {signal['range_low']:.2f} - {signal['range_high']:.2f}\n"
            f"Svecha kattaligi: {signal['candle_range']:.2f} (o'rtacha: {signal['avg_candle_range']:.2f})"
        )
    else:
        emoji, label = "🔴", f"{tf_tag} UPTHRUST (yuqoriga soxta sinish -> mumkin bo'lgan tushish)"
        caption = (
            f"{emoji} {label}\n"
            f"Narx: {price_data['price']} USD\n"
            f"Diapazon: {signal['range_low']:.2f} - {signal['range_high']:.2f}\n"
            f"Svecha kattaligi: {signal['candle_range']:.2f} (o'rtacha: {signal['avg_candle_range']:.2f})"
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


def run_hourly_status(df, price_data, interval="5min"):
    lookback = RANGE_LOOKBACK
    if len(df) < lookback + 1:
        send_telegram_message(f"🕐 [{interval}] Soatlik holat: ma'lumot yetarli emas.")
        return

    window = df.iloc[-(lookback + 1):-1]
    range_low = window["low"].min()
    range_high = window["high"].max()

    range_state = detect_range_state(df, lookback=lookback)

    lines = [
        f"🕐 [{interval}] Soatlik holat",
        f"Narx: {price_data['price']} USD",
        f"Diapazon (so'nggi {lookback} sveча): {range_low:.2f} – {range_high:.2f}",
    ]

    if range_state:
        lines.append(f"\n{RANGE_TYPE_NAMES.get(range_state['type'], range_state['type'])}")
    else:
        lines.append("\n📐 Holat: Aniq diapazon/uchburchak shakli yo'q (trend/keng harakat)")

    lines.append("\nSignal: Hozircha spring/upthrust aniqlanmadi (aniqlansa alohida xabar keladi)")

    try:
        events = get_forex_calendar_events(hours_ahead=24)
        if events:
            lines.append("\n📅 Yaqin 24 soatdagi muhim USD yangiliklari:")
            for e in events[:5]:
                time_str = e["time"].strftime("%d.%m %H:%M UTC")
                extra = ""
                if e["forecast"] or e["previous"]:
                    extra = f" (bashorat: {e['forecast']}, oldingi: {e['previous']})"
                lines.append(f"- {time_str} — {e['title']}{extra}")
        else:
            lines.append("\n📅 Yaqin 24 soatda yuqori ta'sirli USD yangiligi yo'q.")
    except Exception as e:
        lines.append(f"\n⚠️ Kalendar ma'lumoti olinmadi: {e}")

    send_telegram_message("\n".join(lines))
    print(f"[{interval}] Soatlik holat yuborildi.")


# ============================================================================
# ASOSIY DASTUR
# ============================================================================

def main():
    check_env_vars()
    mode = sys.argv[1] if len(sys.argv) > 1 else "signal"
    interval = sys.argv[2] if len(sys.argv) > 2 else "5min"

    try:
        price_data = get_gold_price()
    except Exception as e:
        send_telegram_message(f"⚠️ Narx ma'lumotini olishda xatolik: {e}")
        sys.exit(1)

    try:
        candles_df = get_gold_candles(interval=interval, outputsize=160)
    except Exception as e:
        send_telegram_message(f"⚠️ Sveча ma'lumotini olishda xatolik ({interval}): {e}")
        sys.exit(1)

    if mode == "signal":
        run_signal_check(candles_df, price_data, interval=interval)
    elif mode == "status":
        run_hourly_status(candles_df, price_data, interval=interval)
    else:
        print(f"Noma'lum rejim: {mode}. 'signal' yoki 'status' bo'lishi kerak.")
        sys.exit(1)


if __name__ == "__main__":
    main()