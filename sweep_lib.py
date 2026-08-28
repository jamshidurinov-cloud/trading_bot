"""
sweep_lib.py

"Liquidity Sweep v2" (TradingView, muallif: xriswart, ochiq manba) indikatorining
G'OYASIDAN ilhomlangan, Python'ga moslashtirilgan sweep aniqlash kutubxonasi.

Manba (tushuntirish uchun o'qilgan, Pine Script kodi nusxa ko'chirilmagan - bu
yerdagi barcha kod o'zimiz Python'da qayta yozgan): 
https://www.tradingview.com/script/np2XeabS-Liquidity-Sweep-v2/

HALI BOTGA ULANMAGAN - main.py yoki jackpot_signal.py hech narsani bu fayldan
import qilmaydi. Bu - keyinroq (200 ta signaldan keyin, statistika asosida qaror
qabul qilingach) ulash uchun tayyorlab qo'yilgan, mustaqil, sinalgan modul.

=== ASOSIY G'OYA (asl indikatordan) ===

1. FAQAT BITTA FAOL DARAJA (har tomonda):
   Har tomonda (yuqori/resistance, past/support) doim FAQAT BITTA "faol"
   (eng so'nggi, hali sweep qilinmagan) daraja kuzatiladi. Yangi swing paydo
   bo'lsa, eski (hali sweep qilinmagan) daraja AVTOMATIK almashtiriladi.
   Bu - bizning smc_lib.py'dagi "eski, unutilgan sweep" muammosini strukturaviy
   jihatdan butunlay yo'q qiladi (chunki eski darajalar umuman saqlanmaydi).

2. MUDDATI O'TISH:
   Agar faol daraja `expiry_bars` sveцha davomida sweep qilinmasa - o'zi
   "unutiladi" (yangi swing kutiladi).

3. TRAP vs BREAK farqi:
   Narx faol darajani kesib o'tganda:
   - Agar svecha ASL TARAFGA QAYTIB yopilsa -> "TRAP" (haqiqiy sweep/liquidity
     grab - stop'lar ishga tushirilgan, lekin harakat darhol rad etilgan)
   - Agar svecha O'SHA TARAFDA QOLSA (yopilish ham o'tib ketgan) -> "BREAK"
     (chinakam struktura sinishi, sweep EMAS - trend davomiyligi belgisi)

   TRAP - bizning asosiy signal uchun kerakli holat (teskari burilish setup'i).
   BREAK - bu sweep emas, shuning uchun signal sifatida ishlatilmaydi.

=== ISHLATILISHI (keyinroq, ulanganda) ===

    from sweep_lib import detect_sweep_events, get_latest_active_levels

    events = detect_sweep_events(df, swing_window=6, expiry_bars=100)
    traps = [e for e in events if e["type"] == "trap"]
    # eng so'nggi trap - eng dolzarb sweep
"""

import pandas as pd
import numpy as np


def find_pivots(highs, lows, window=5):
    """Standart pivot (swing) aniqlash: har nuqta atrofida `window` sveцha
    oldin va keyin ichida eng ekstremal bo'lsa - tasdiqlangan swing deb
    belgilaydi. Oxirgi `window` ta sveцha hali tasdiqlanolmaydi (kelajak
    ma'lumoti yetarli emas) - bu barcha shunga o'xshash usullar uchun tabiiy
    kechikish."""
    n = len(highs)
    swing_high_idx, swing_low_idx = [], []
    for i in range(window, n - window):
        lo, hi = i - window, i + window + 1
        if highs[i] == highs[lo:hi].max():
            swing_high_idx.append(i)
        if lows[i] == lows[lo:hi].min():
            swing_low_idx.append(i)
    return swing_high_idx, swing_low_idx


def detect_sweep_events(df, swing_window=5, expiry_bars=100, confirmation="close"):
    """"Liquidity Sweep v2" mantig'i asosida barcha sweep voqealarini (Trap va
    Break) topadi.

    Parametrlar:
        swing_window: pivot tasdiqlash oynasi (har tarafda necha sveцha)
        expiry_bars: faol daraja necha sveцhadan keyin "muddati o'tadi"
        confirmation: "wick" (shoxi tegishi bilanoq) yoki "close" (yopilish
                      ham darajadan o'tishi kerak, Break uchun qattiqroq)

    Qaytaradi: vaqt bo'yicha tartiblangan lug'atlar ro'yxati:
        {type: "trap"/"break", direction: "bullish"/"bearish", level: narx,
         level_time: daraja hosil bo'lgan vaqt, event_time: sweep vaqti,
         event_idx: sveцha indeksi}
    """
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    times = df.index
    n = len(df)

    swing_high_idx, swing_low_idx = find_pivots(highs, lows, window=swing_window)
    swing_high_set = set(swing_high_idx)
    swing_low_set = set(swing_low_idx)

    events = []

    # --- YUQORI TOMON (resistance) ---
    active_idx = None
    active_level = None
    for i in range(n):
        # 1) AVVAL: joriy faol darajaga tegib-tegmaganini tekshiramiz (agar shu
        # svechaning o'zi ham yangi swing bo'lsa ham, avval eskisini tekshiramiz)
        if active_level is not None:
            if i - active_idx > expiry_bars:
                active_idx = None
                active_level = None
            elif highs[i] >= active_level:
                if confirmation == "close" and closes[i] < active_level:
                    kind = "trap"
                elif confirmation == "close":
                    kind = "break"
                else:
                    kind = "trap" if closes[i] < active_level else "break"
                events.append({
                    "type": kind,
                    "direction": "bearish" if kind == "trap" else "bullish",
                    "level": float(active_level),
                    "level_time": str(times[active_idx]),
                    "event_time": str(times[i]),
                    "event_idx": i,
                })
                active_idx = None
                active_level = None

      
