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
WYCKOFF_CONFIRM_WINDOW = 5  # SMC signal chiqqanda, undan necha sveчa oldingacha
                            # mos Spring/Upthrust qidirilsin (tasdiq filtri uchun)


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
    ko'p ta'sir qiladigan voqealar). Diqqat: bu manzilga 5 daqiqada faqat 2 ma
