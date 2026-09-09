"""
luxalgo_liquidity_sweeps.py

LuxAlgo "Liquidity Sweeps [LuxAlgo]" Pine Script indikatorining ANIQ Python
porti. Manba: foydalanuvchi tomonidan TradingView Pine Editor'idan
to'g'ridan-to'g'ri nusxalab olingan, to'liq (@version=5) kod.

Litsenziya: CC BY-NC-SA 4.0 (Attribution-NonCommercial-ShareAlike) - © LuxAlgo
DIQQAT: NonCommercial (notijorat) shart bilan.

=== ASOSIY MANTIQ (Pine kodidan aniq o'qib olingan) ===

Har bir swing pivot (yuqori yoki past, standart markazlashgan oyna:
ta.pivothigh/pivotlow(len,len)) UCH XIL holatni ketma-ket boshidan
kechirishi mumkin:

1. "WICK SWEEP" (darhol rad etish): narx pivot darajasini SHOX bilan
   kesib o'tadi, lekin YOPILISH darajadan pastda (yuqori pivot uchun)
   yoki yuqorida (past pivot uchun) qoladi - bu, klassik "stop raid".
   Bu - "not oO" (ya'ni "Only Wicks" yoki "Wicks+Outbreaks" rejimida)
   ishlaydi, va pivotning "brk" (broken) holatidan MUSTAQIL ravishda
   tekshiriladi.

2. "BROKEN" (yopilish orqali sinish): narx YOPILISHDA pivot darajasidan
   o'tadi. Agar rejim "Only Wicks" bo'lsa - bu darhol "mitigated"
   (e'tiborsiz qoldiriladi). Aks holda - pivot "brk=true" holatiga
   o'tadi, va endi RETEST kutiladi.

3. "OUTBREAK & RETEST" (2-holatdan keyin): "brk=true" bo'lgan pivot,
   keyingi barlarda narx yopilishda darajaga QAYTIB, uni "qayta sinasa"
   (retest) va SHOX bilan teskari yo'nalishda rad etsa - bu "tak=true"
   (taken) - to'liq tasdiqlangan outbreak+retest signali. Agar narx
   shunchaki yopilishda darajadan qaytib o'tib ketsa (retest muvaffaqiyatsiz)
   - "mit=true" (mitigated, pivot e'tiborsiz qoldiriladi).

Har bir signal (wick sweep yoki outbreak+retest) uchun "Sweep Area"
(pivot darajasi va signal svechasining eng chekka nuqtasi orasidagi zona)
hisoblanadi - bu SL/Entry uchun tayyor zona sifatida ishlatilishi mumkin.

HALI BOTGA ULANMAGAN - main.py hech narsani bu fayldan import qilmaydi.
"""

import numpy as np
import pandas as pd


def pivot_high_low(highs, lows, length):
    """Pine: ta.pivothigh(len,len) / ta.pivotlow(len,len) - markazlashgan
    oyna: bar i, [i-length, i+length] ichida eng ekstremal bo'lsa - pivot.
    Natija faqat i+length barida "biladi" (kelajak malumoti kerak)."""
    n = len(highs)
    piv_high = np.full(n, np.nan)
    piv_low = np.full(n, np.nan)
    for i in range(length, n - length):
        window_h = highs[i - length:i + length + 1]
        window_l = lows[i - length:i + length + 1]
        if highs[i] == window_h.max():
            piv_high[i] = highs[i]
        if lows[i] == window_l.min():
            piv_low[i] = lows[i]
    return piv_high, piv_low


def detect_luxalgo_liquidity_sweeps(df, length=5, mode="only_wicks", max_age=2000):
    """LuxAlgo "Liquidity Sweeps" asosiy tsikli - PIVOT DARAJALARINI
    kuzatib, WICK SWEEP va OUTBREAK+RETEST signallarini topadi.

    mode: "only_wicks" | "only_outbreak_retest" | "wicks_and_outbreak_retest"
    max_age: pivot shuncha bardan keyin ham hal bolmasa, "eskirgan" deb
             tashlab yuboriladi (Pine kodida qattiq 2000 deb yozilgan).

    Qaytaradi: events ro'yxati, har biri:
      {kind: 'wick_sweep' / 'outbreak_retest',
       direction: 'bullish' / 'bearish',
       pivot_level:, pivot_idx:, signal_idx: (signal sodir bolgan svecha),
       sweep_area_top:, sweep_area_bottom:}
    """
    oW = mode == "only_wicks"
    oO = mode == "only_outbreak_retest"

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    n = len(df)

    piv_high, piv_low = pivot_high_low(highs, lows, length)

    events = []

    # --- YUQORI PIVOTLAR (resistance) ---
    active_highs = []  # har biri: {level, bix, brk, mit, tak, wic}
    for i in range(n):
        if not np.isnan(piv_high[i]):
            active_highs.append({"level": float(piv_high[i]), "bix": i, "brk": False,
                                  "mit": False, "tak": False, "wic": False})

        for p in active_highs:
            if p["mit"] or p["tak"]:
                continue
            if not p["brk"]:
                if closes[i] > p["level"]:
                    if not oW:
                        p["brk"] = True
                    else:
                        p["mit"] = True
                if (not oO) and (not p["wic"]):
                    if highs[i] > p["level"] and closes[i] < p["level"]:
                        events.append({
                            "kind": "wick_sweep", "direction": "bearish",
                            "pivot_level": p["level"], "pivot_idx": p["bix"], "signal_idx": i,
                            "sweep_area_top": float(highs[i]), "sweep_area_bottom": p["level"],
                        })
                        p["wic"] = True
            else:
                if closes[i] < p["level"]:
                    p["mit"] = True
                if (not oW) and lows[i] < p["level"] and closes[i] > p["level"]:
                    events.append({
                        "kind": "outbreak_retest", "direction": "bullish",
                        "pivot_level": p["level"], "pivot_idx": p["bix"], "signal_idx": i,
                        "sweep_area_top": p["level"], "sweep_area_bottom": float(lows[i]),
                    })
                    p["tak"] = True

        active_highs = [p for p in active_highs
                        if not (i - p["bix"] > max_age or p["mit"] or p["tak"])]

    # --- PAST PIVOTLAR (support) - yuqoridagining aynan simmetrik ko'zgusi ---
    active_lows = []
    for i in range(n):
        if not np.isnan(piv_low[i]):
            active_lows.append({"level": float(piv_low[i]), "bix": i, "brk": False,
                                 "mit": False, "tak": False, "wic": False})

        for p in active_lows:
            if p["mit"] or p["tak"]:
                continue
            if not p["brk"]:
                if closes[i] < p["level"]:
                    if not oW:
                        p["brk"] = True
                    else:
                        p["mit"] = True
                if (not oO) and (not p["wic"]):
                    if lows[i] < p["level"] and closes[i] > p["level"]:
                        events.append({
                            "kind": "wick_sweep", "direction": "bullish",
                            "pivot_level": p["level"], "pivot_idx": p["bix"], "signal_idx": i,
                            "sweep_area_top": p["level"], "sweep_area_bottom": float(lows[i]),
                        })
                        p["wic"] = True
            else:
                if closes[i] > p["level"]:
                    p["mit"] = True
                if (not oW) and highs[i] > p["level"] and closes[i] < p["level"]:
                    events.append({
                        "kind": "outbreak_retest", "direction": "bearish",
                        "pivot_level": p["level"], "pivot_idx": p["bix"], "signal_idx": i,
                        "sweep_area_top": float(highs[i]), "sweep_area_bottom": p["level"],
                    })
                    p["tak"] = True

        active_lows = [p for p in active_lows
                       if not (i - p["bix"] > max_age or p["mit"] or p["tak"])]

    events.sort(key=lambda e: e["signal_idx"])
    return events
