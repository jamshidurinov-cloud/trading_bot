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
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")      # signal tracking uchun (Gist)
GIST_ID = os.environ.get("GIST_ID")                # signal tracking uchun (Gist)

REQUIRED_VARS = {
    "TWELVEDATA_API_KEY": TWELVEDATA_API_KEY,
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

    def to_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return v

    return {
        "price": to_float(data.get("close")),
        "change": to_float(data.get("change")),
        "percent_change": to_float(data.get("percent_change")),
        "high": to_float(data.get("high")),
        "low": to_float(data.get("low")),
        "volume": to_float(data.get("volume")),
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


def get_trend_bias(df, period=50, threshold_pct=0.05):
    """Mavjud ma'lumot asosida (qo'shimcha API so'rovisiz) umumiy trend yo'nalishini
    taxminan aniqlaydi: joriy narxni so'nggi `period` sveчaning o'rtacha narxi bilan
    solishtiradi. Bu haqiqiy yuqori timeframe emas, balki tezkor va bepul proksi."""
    if len(df) < period:
        return "neutral"
    sma = df["close"].tail(period).mean()
    current = df["close"].iloc[-1]
    if sma == 0:
        return "neutral"
    diff_pct = (current - sma) / sma * 100
    if diff_pct > threshold_pct:
        return "bullish"
    elif diff_pct < -threshold_pct:
        return "bearish"
    return "neutral"


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
# SIGNAL TRACKING (GitHub Gist orqali - Cron Job'lar orasida "yodda tutish" uchun)
# ============================================================================
# Render Cron Job'lari har safar yangidan boshlanadi (hech narsani "yodda tutmaydi"),
# shuning uchun signal tarixini tashqi joyda (kichik GitHub Gist fayli) saqlaymiz.
# Agar GITHUB_TOKEN/GIST_ID sozlanmagan bo'lsa, tracking jim o'tkazib yuboriladi
# (bot signal berishda davom etadi, faqat statistika yig'ilmaydi).

GIST_API_URL = "https://api.github.com/gists"
CHECK_AFTER_MINUTES = 60   # signal chiqqandan necha daqiqadan keyin natijasini tekshirish
OUTCOME_THRESHOLD_PCT = 0.05  # neytral/g'olib/mag'lub chegarasi (narx foizda necha % siljishi kerak)


def tracking_enabled():
    return bool(GITHUB_TOKEN and GIST_ID)


def load_signal_log():
    """Gist'dan signal tarixini o'qiydi. Muammo bo'lsa bo'sh ro'yxat qaytaradi."""
    if not tracking_enabled():
        return []
    try:
        resp = requests.get(
            f"{GIST_API_URL}/{GIST_ID}",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["files"]["signals.json"]["content"]
        import json
        return json.loads(content)
    except Exception as e:
        print(f"Signal logni o'qishda xatolik: {e}")
        return []


def save_signal_log(log):
    """Signal tarixini Gist'ga qaytadan yozadi."""
    if not tracking_enabled():
        return
    import json
    try:
        resp = requests.patch(
            f"{GIST_API_URL}/{GIST_ID}",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
            json={"files": {"signals.json": {"content": json.dumps(log, indent=2, ensure_ascii=False)}}},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"Signal logni saqlashda xatolik: {e}")


def log_new_signal(signal, price_data, interval):
    """Yangi chiqqan signalni tarixga qo'shadi (natijasi keyinroq tekshiriladi)."""
    if not tracking_enabled():
        return
    import datetime as dt

    direction = "bullish" if signal["type"] in ("smc_bullish", "spring") else "bearish"
    log = load_signal_log()
    log.append({
        "time": dt.datetime.now(dt.timezone.utc).isoformat(),
        "type": signal["type"],
        "interval": interval,
        "direction": direction,
        "entry_price": price_data["price"],
        "checked": False,
        "outcome": None,
    })
    save_signal_log(log)


def evaluate_pending_signals(current_price):
    """Muddati o'tgan, hali tekshirilmagan signallarni tekshirib, g'olib/mag'lub/neytral
    deb belgilaydi. Umumiy statistikani qaytaradi."""
    if not tracking_enabled():
        return None

    import datetime as dt

    log = load_signal_log()
    now = dt.datetime.now(dt.timezone.utc)
    changed = False

    for entry in log:
        if entry.get("checked"):
            continue
        try:
            signal_time = dt.datetime.fromisoformat(entry["time"])
        except (ValueError, KeyError):
            continue

        if (now - signal_time).total_seconds() < CHECK_AFTER_MINUTES * 60:
            continue  # hali muddati kelmagan

        try:
            entry_price = float(entry["entry_price"])
            current_price = float(current_price)
        except (TypeError, ValueError):
            continue
        pct_change = (current_price - entry_price) / entry_price * 100

        if entry["direction"] == "bullish":
            outcome = "win" if pct_change > OUTCOME_THRESHOLD_PCT else (
                "loss" if pct_change < -OUTCOME_THRESHOLD_PCT else "neutral")
        else:
            outcome = "win" if pct_change < -OUTCOME_THRESHOLD_PCT else (
                "loss" if pct_change > OUTCOME_THRESHOLD_PCT else "neutral")

        entry["checked"] = True
        entry["outcome"] = outcome
        changed = True

    if changed:
        save_signal_log(log)

    checked = [e for e in log if e.get("checked")]
    wins = sum(1 for e in checked if e["outcome"] == "win")
    losses = sum(1 for e in checked if e["outcome"] == "loss")
    neutral = sum(1 for e in checked if e["outcome"] == "neutral")
    total = len(checked)
    win_rate = round(wins / total * 100, 1) if total else None

    return {
        "total_checked": total,
        "wins": wins,
        "losses": losses,
        "neutral": neutral,
        "win_rate": win_rate,
        "pending": len(log) - total,
    }


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

    tf_tag = f"[{interval}]"
    bias = get_trend_bias(df)
    signal_direction = "bullish" if signal["type"] in ("smc_bullish", "spring") else "bearish"
    trend_warning = ""
    if bias != "neutral" and bias != signal_direction:
        bias_uz = "YUQORIGA (bullish)" if bias == "bullish" else "PASTGA (bearish)"
        trend_warning = (
            f"\n\n⚠️ DIQQAT: umumiy narx harakati {bias_uz} yo'nalishda "
            f"(so'nggi {min(50, len(df))} sveчa o'rtachasiga nisbatan) — bu signal "
            f"UMUMIY TREND'GA QARSHI bo'lishi mumkin, ehtiyot bo'ling."
        )

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

    caption += trend_warning
    send_telegram_document(chart_path, caption=caption)

    # Signal tarixga yoziladi - natijasi keyinroq (soatlik status'da) tekshiriladi
    log_new_signal(signal, price_data, interval)

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

    if tracking_enabled():
        try:
            stats = evaluate_pending_signals(price_data["price"])
            if stats and stats["total_checked"] > 0:
                lines.append(
                    f"\n📈 Signal statistikasi: {stats['total_checked']} ta signal tekshirildi — "
                    f"✅ {stats['wins']} g'olib, ❌ {stats['losses']} mag'lub, "
                    f"➖ {stats['neutral']} neytral (aniqlik: {stats['win_rate']}%)"
                )
                if stats["pending"] > 0:
                    lines.append(f"⏳ Hali natijasi kutilayotgan signallar: {stats['pending']}")
            elif stats:
                lines.append(f"\n📈 Signal statistikasi: hali yetarli tekshirilgan signal yo'q (kutilmoqda: {stats['pending']}).")
        except Exception as e:
            lines.append(f"\n⚠️ Signal statistikasini olishda xatolik: {e}")

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
