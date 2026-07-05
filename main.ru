"""
Tilla (XAUUSD) narxi va yangiliklarini kuzatuvchi, AI tahlil qiluvchi Telegram bot.

Bu skript bir marta ishga tushadi, tekshiradi, xabar yuboradi va tugaydi.
Render'da "Cron Job" sifatida har 15-30 daqiqada avtomatik ishga tushiriladi.
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


def check_env_vars():
    missing = [name for name, value in REQUIRED_VARS.items() if not value]
    if missing:
        print(f"XATOLIK: quyidagi environment variable'lar topilmadi: {', '.join(missing)}")
        sys.exit(1)


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


def get_gold_candles(interval="15min", outputsize=100):
    """TwelveData'dan oxirgi svechalar tarixini (OHLCV) oladi, grafik chizish uchun."""
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
            df[col] = 0.0  # ba'zi tariflarda volume kelmasligi mumkin

    return df


def make_chart_image(df, path="/tmp/chart.png"):
    """OHLCV ma'lumotidan candlestick + volume grafik chizib, faylga saqlaydi."""
    import mplfinance as mpf

    mpf.plot(
        df,
        type="candle",
        volume=True,
        style="charles",
        title="XAUUSD - so'nggi svechalar",
        savefig=dict(fname=path, dpi=150, bbox_inches="tight"),
    )
    return path


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
    resp.raise_for_status()
    data = resp.json()

    articles = data.get("articles", [])
    headlines = []
    for a in articles[:5]:
        title = a.get("title", "")
        source = a.get("source", {}).get("name", "")
        if title:
            headlines.append(f"- {title} ({source})")

    return headlines


def analyze_with_claude(chart_path, price_data, headlines):
    """Grafik rasmi, narx va yangiliklarni Claude API'ga yuborib,
    SMC/ICT/Wyckoff/hajm nuqtai nazaridan tahlil oldiradi."""
    import base64

    news_text = "\n".join(headlines) if headlines else "Yangilik topilmadi."

    with open(chart_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    prompt = f"""Sen SMC (Smart Money Concepts), ICT, Wyckoff va hajm (volume) tahliliga ixtisoslashgan
treyder-tahlilchisan. Ilova qilingan XAUUSD grafigini shu metodlar nuqtai nazaridan tahlil qil:

- Market structure (BOS/CHoCH bo'lishi mumkinmi)
- Ehtimoliy order block yoki fair value gap zonalari
- Liquidity zonalari (qayerda stop-loss'lar to'planishi mumkin)
- Wyckoff bosqichi (accumulation/distribution/markup/markdown belgilarimi)
- Hajm (volume) tasdiqlaydimi yoki rad etadimi

Javobni o'zbek tilida, Telegram xabari uchun mos, qisqa va aniq formatda yoz (bo'limlarga bo'lib).
Bashorat yoki "sotib ol/sot" degan tavsiya berma — faqat kuzatuv va e'tibor qaratish kerak bo'lgan
narsalarni ayt. Oxirida "Bu tavsiya emas, faqat texnik kuzatuv" deb yoz.

Qo'shimcha ma'lumot:
Joriy narx: {price_data['price']}
O'zgarish: {price_data['change']} ({price_data['percent_change']}%)
Kunlik yuqori/past: {price_data['high']} / {price_data['low']}

So'nggi yangiliklar:
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
            "max_tokens": 700,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
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
    text_blocks = [block["text"] for block in data.get("content", []) if block.get("type") == "text"]
    return "\n".join(text_blocks).strip()


def send_telegram_message(text):
    """Telegram bot orqali matnli xabar yuboradi."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=15)
    resp.raise_for_status()


def send_telegram_photo(photo_path, caption=""):
    """Telegram bot orqali grafik rasmini (va tavsifni) yuboradi.
    Telegram caption uzunligi cheklangan (1024 belgi), shuning uchun uzun bo'lsa
    rasm qisqa izoh bilan, keyin to'liq tahlil alohida xabar sifatida yuboriladi."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    short_caption = caption[:1000] if caption else ""
    with open(photo_path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": short_caption},
            files={"photo": f},
            timeout=30,
        )
    resp.raise_for_status()

    if caption and len(caption) > 1000:
        send_telegram_message(caption)


def main():
    check_env_vars()

    try:
        price_data = get_gold_price()
    except Exception as e:
        send_telegram_message(f"⚠️ Narx ma'lumotini olishda xatolik: {e}")
        sys.exit(1)

    try:
        candles_df = get_gold_candles(interval="5min", outputsize=100)
        chart_path = make_chart_image(candles_df)
    except Exception as e:
        send_telegram_message(f"⚠️ Grafik yaratishda xatolik: {e}")
        sys.exit(1)

    try:
        headlines = get_gold_news()
    except Exception as e:
        print(f"Yangilik olishda xatolik (davom etamiz): {e}")
        headlines = []

    try:
        analysis = analyze_with_claude(chart_path, price_data, headlines)
    except Exception as e:
        send_telegram_message(f"⚠️ AI tahlilida xatolik: {e}")
        sys.exit(1)

    caption = (
        f"🥇 XAUUSD Yangilanishi\n"
        f"Narx: {price_data['price']} USD "
        f"({price_data['change']}, {price_data['percent_change']}%)\n\n"
        f"📊 SMC/ICT/Wyckoff Tahlili:\n{analysis}"
    )

    send_telegram_photo(chart_path, caption=caption)
    print("Grafik va tahlil muvaffaqiyatli yuborildi.")


if __name__ == "__main__":
    main()
