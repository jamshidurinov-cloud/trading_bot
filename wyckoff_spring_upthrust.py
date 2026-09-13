"""
wyckoff_spring_upthrust.py
-----------------------------
QuantVue "Wyckoff Springs" (Pine Script v5) indikatorining ANIQ, bar-by-bar
Python portlanishi (Spring - aniq portlash; Upthrust - simmetrik kengaytma).

Manba: QuantVue, Mozilla Public License 2.0 (https://mozilla.org/MPL/2.0/).
Asl Pine kodi ushbu faylning oxirida, izoh sifatida saqlangan.

MUHIM: bu fayl - boshqa "lib" fayllar (luxalgo_smc.py, luxalgo_liquidity_sweeps.py,
luxalgo_range_detector.py) bilan bir xil qoidaga bo'ysunadi: ANIQ portlangan,
o'zgartirilmagan. Bizning tizimga moslashtirish (masalan bu yerdagi
`pivotlow`/`highest`/`lowest` range'i o'rniga bizning `luxalgo_range_detector`
natijasini ishlatish) - ALOHIDA moslashtiruvchi faylda bo'ladi.

=== Pine mantig'i (asl kod bo'yicha, qadam-baqadam tushuntirish) ===

1. `pivotlow(pivlen, pivlen)` - klassik swing-low: bar `j`ning `low`si,
   chap tomonda `pivlen` ta VA o'ng tomonda `pivlen` ta bardan PASTROQ (yoki
   teng eng kichik) bo'lsa - bu pivot. MUHIM: bu faqat `pivlen` bar
   O'TGANDAN KEYIN "tasdiqlanadi" (chunki o'ng tomonni bilish uchun shuncha
   bar kerak) - ya'ni HECH QACHON "kelajakni oldindan bilish" (repaint)
   muammosi yo'q, portlashda ham shu tartib saqlanadi.

2. Har bir tasdiqlangan pivot - RO'YXATGA qo'shiladi va CHEKSIZ vaqt
   davomida "faol" bo'lib qoladi (hech qachon o'zi eskirib qolmaydi).

3. Har bir KEYINGI barda, har bir FAOL pivot uchun tekshiriladi:
   - Agar (shu barning low'i pivotdan PAST) VA (shu barning close'i
     pivotdan YUQORI - ya'ni qaytib chiqdi) VA (hozirgacha muvaffaqiyatsiz
     urinishlar soni <= 3) VA (shu barning low'i - oxirgi `rangePeriod`
     bar ichidagi ENG PASTKI narx bilan barobar yoki undan past, ya'ni bu
     haqiqatan ham "yangi, chuqur" nuqta) VA (agar talab qilinsa - hajm
     ham yetarli) -> SPRING TASDIQLANDI, bu pivot endi FAOLSIZ qilinadi.
   - Aks holda, agar shu barning low'i pivotdan past bo'lsa (lekin
     yuqoridagi barcha shartlar birga bajarilmasa) -> "muvaffaqiyatsiz
     urinish" hisoblagichi +1 oshadi.
   - MUHIM (Jamshid bilan tasdiqlangan): bu hisoblagich VAQT (necha bar
     o'tgani) emas, balki "necha marta pivotdan pastga tushib, lekin
     qaytmagan" sonini sanaydi. Pivotga UMUMAN tegmagan (past qilmagan)
     barlar - hisoblanmaydi, hisoblagich o'zgarmaydi.

Bearish (Upthrust) versiyasi - bu Pine kodida YO'Q (faqat Spring, ya'ni
bullish tomon bor). Quyida, simmetrik mantiq bilan, Upthrust versiyasi
ham QO'SHILDI (chunki bizga ikkala yo'nalish ham kerak) - bu qism ANIQ
portlash emas, balki asl Pine kodining o'ziga SIMMETRIK ravishda
qurilgan kengaytma, alohida belgilab qo'yilgan.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _find_confirmed_pivot_lows(lows: np.ndarray, pivlen: int) -> list:
    """Pine'ning ta.pivotlow(pivlen, pivlen) bilan bir xil: bar j - agar
    uning low'i, chap va o'ng tomondagi pivlen ta bardan (ikkala tomon
    ham) PAST YOKI TENG bo'lsa - pivot. Faqat j+pivlen barda "tasdiqlanadi"
    (Pine'dagi kabi, oldinga qarab bilish yo'q).

    Qaytaradi: (pivot_bar_idx, confirm_bar_idx, level) tuple'lar ro'yxati,
    confirm_bar_idx bo'yicha saralangan (xronologik, portlash uchun qulay)."""
    n = len(lows)
    out = []
    for j in range(pivlen, n - pivlen):
        window = lows[j - pivlen: j + pivlen + 1]
        if lows[j] == window.min():
            out.append((j, j + pivlen, float(lows[j])))
    out.sort(key=lambda t: t[1])
    return out


def _find_confirmed_pivot_highs(highs: np.ndarray, pivlen: int) -> list:
    """Yuqoridagining aynan simmetrik ko'zgusi (Upthrust uchun, kengaytma)."""
    n = len(highs)
    out = []
    for j in range(pivlen, n - pivlen):
        window = highs[j - pivlen: j + pivlen + 1]
        if highs[j] == window.max():
            out.append((j, j + pivlen, float(highs[j])))
    out.sort(key=lambda t: t[1])
    return out


def detect_wyckoff_springs(df: pd.DataFrame, pivlen: int = 6, range_period: int = 20,
                             max_attempts: int = 3, require_volume: bool = False,
                             volume_threshold: float = 1.5, external_level_series=None) -> list:
    """QuantVue "Wyckoff Springs" - ANIQ portlash (faqat bullish/Spring tomoni,
    asl Pine kodidagidek).

    external_level_series (YANGI, ixtiyoriy): agar berilsa (har bir bar uchun
    bitta qiymat - masalan bizning luxalgo_range_detector'ning box_bottom
    natijasi, faol range yo'q joylarda NaN/None), bu holda ICHKI pivotlow
    qidiruvi (pivlen asosida) UMUMAN ISHLATILMAYDI - "daraja" to'g'ridan-
    to'g'ri shu tashqi qatordan olinadi. Daraja qiymati o'ZGARGANDA (yangi
    range yoki kengaytirilgan range) - bu YANGI "pivot" deb hisoblanadi,
    eski hisoblagich (urinishlar soni) TASHLAB YUBORILADI va 0dan boshlanadi.
    Bu rejimda faqat BITTA joriy daraja kuzatiladi (bizning range'imizda ham
    har doim bitta joriy box borligi kabi) - `low<=range_low` (recent-extreme)
    sharti bu rejimda QO'LLANILMAYDI, chunki daraja allaqachon tashqi manba
    (masalan range detektorimiz) tomonidan tasdiqlangan haqiqiy chegara.

    Qaytaradi: har bir tasdiqlangan Spring uchun bitta lug'at (dict), list
    ichida:
        {
            "kind": "spring",
            "pivot_idx": ...,       # pivot (nomzod) topilgan bar
            "pivot_level": ...,     # pivot narx darajasi
            "confirm_idx": ...,     # SPRING TASDIQLANGAN bar (signal shu yerda)
            "attempts_used": ...,   # tasdiqlanguncha nechta muvaffaqiyatsiz urinish bo'lgani
        }
    """
    n = len(df)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    volumes = df["volume"].to_numpy(dtype=float) if "volume" in df.columns else None
    avg_vol = (pd.Series(volumes).rolling(range_period, min_periods=range_period).mean().to_numpy(dtype=float)
               if volumes is not None else None)

    events = []

    if external_level_series is not None:
        ext = np.asarray(external_level_series, dtype=float)
        if len(ext) != n:
            raise ValueError("external_level_series uzunligi df bilan bir xil bo'lishi kerak")

        current_piv = None  # {"pivot_idx", "level", "active", "count"} - bitta joriy daraja
        prev_level = None

        for i in range(n):
            level_i = ext[i]
            has_level = not np.isnan(level_i)

            if has_level and (prev_level is None or level_i != prev_level):
                # Daraja YANGI yoki O'ZGARGAN - yangi pivot, eski hisoblagich tashlanadi
                current_piv = {"pivot_idx": i, "level": float(level_i), "active": True, "count": 0}
            prev_level = level_i if has_level else None

            if current_piv is not None and current_piv["active"]:
                vol_ok = True
                if require_volume and avg_vol is not None and not np.isnan(avg_vol[i]):
                    vol_ok = volumes[i] >= avg_vol[i] * volume_threshold

                if (lows[i] < current_piv["level"] and closes[i] > current_piv["level"]
                        and current_piv["count"] <= max_attempts and vol_ok):
                    events.append({
                        "kind": "spring",
                        "pivot_idx": current_piv["pivot_idx"],
                        "pivot_level": current_piv["level"],
                        "confirm_idx": i,
                        "attempts_used": current_piv["count"],
                    })
                    current_piv["active"] = False
                elif lows[i] < current_piv["level"]:
                    current_piv["count"] += 1

        return events

    # --- ASL (pivotlow-asoslangan) yo'l, o'zgarishsiz ---
    range_low = df["low"].rolling(range_period, min_periods=range_period).min().to_numpy(dtype=float)

    pivots = _find_confirmed_pivot_lows(lows, pivlen)
    pivots_by_confirm_bar = {}
    for pivot_idx, confirm_idx, level in pivots:
        pivots_by_confirm_bar.setdefault(confirm_idx, []).append(
            {"pivot_idx": pivot_idx, "level": level, "active": True, "count": 0}
        )

    active_pivs = []

    for i in range(n):
        if i in pivots_by_confirm_bar:
            active_pivs.extend(pivots_by_confirm_bar[i])

        if np.isnan(range_low[i]):
            continue

        for p in active_pivs:
            if not p["active"]:
                continue

            vol_ok = True
            if require_volume and avg_vol is not None and not np.isnan(avg_vol[i]):
                vol_ok = volumes[i] >= avg_vol[i] * volume_threshold

            if (lows[i] < p["level"] and closes[i] > p["level"] and p["count"] <= max_attempts
                    and lows[i] <= range_low[i] and vol_ok):
                events.append({
                    "kind": "spring",
                    "pivot_idx": p["pivot_idx"],
                    "pivot_level": p["level"],
                    "confirm_idx": i,
                    "attempts_used": p["count"],
                })
                p["active"] = False
            elif lows[i] < p["level"]:
                p["count"] += 1

        active_pivs = [p for p in active_pivs if p["active"]]

    return events


def detect_wyckoff_upthrusts(df: pd.DataFrame, pivlen: int = 6, range_period: int = 20,
                               max_attempts: int = 3, require_volume: bool = False,
                               volume_threshold: float = 1.5, external_level_series=None) -> list:
    """Upthrust (bearish) - asl Pine kodida YO'Q, Spring mantig'iga SIMMETRIK
    ravishda qurilgan kengaytma (bu qism "aniq portlash" emas, balki asl
    muallif bergan mantiqning oynadagi aksi).

    external_level_series - detect_wyckoff_springs'dagi bilan bir xil g'oya
    (masalan bizning range detektorimizning box_top natijasi), simmetrik
    ravishda qo'llaniladi."""
    n = len(df)
    highs = df["high"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    volumes = df["volume"].to_numpy(dtype=float) if "volume" in df.columns else None
    avg_vol = (pd.Series(volumes).rolling(range_period, min_periods=range_period).mean().to_numpy(dtype=float)
               if volumes is not None else None)

    events = []

    if external_level_series is not None:
        ext = np.asarray(external_level_series, dtype=float)
        if len(ext) != n:
            raise ValueError("external_level_series uzunligi df bilan bir xil bo'lishi kerak")

        current_piv = None
        prev_level = None

        for i in range(n):
            level_i = ext[i]
            has_level = not np.isnan(level_i)

            if has_level and (prev_level is None or level_i != prev_level):
                current_piv = {"pivot_idx": i, "level": float(level_i), "active": True, "count": 0}
            prev_level = level_i if has_level else None

            if current_piv is not None and current_piv["active"]:
                vol_ok = True
                if require_volume and avg_vol is not None and not np.isnan(avg_vol[i]):
                    vol_ok = volumes[i] >= avg_vol[i] * volume_threshold

                if (highs[i] > current_piv["level"] and closes[i] < current_piv["level"]
                        and current_piv["count"] <= max_attempts and vol_ok):
                    events.append({
                        "kind": "upthrust",
                        "pivot_idx": current_piv["pivot_idx"],
                        "pivot_level": current_piv["level"],
                        "confirm_idx": i,
                        "attempts_used": current_piv["count"],
                    })
                    current_piv["active"] = False
                elif highs[i] > current_piv["level"]:
                    current_piv["count"] += 1

        return events

    # --- ASL (pivothigh-asoslangan) yo'l, o'zgarishsiz ---
    range_high = df["high"].rolling(range_period, min_periods=range_period).max().to_numpy(dtype=float)

    pivots = _find_confirmed_pivot_highs(highs, pivlen)
    pivots_by_confirm_bar = {}
    for pivot_idx, confirm_idx, level in pivots:
        pivots_by_confirm_bar.setdefault(confirm_idx, []).append(
            {"pivot_idx": pivot_idx, "level": level, "active": True, "count": 0}
        )

    active_pivs = []

    for i in range(n):
        if i in pivots_by_confirm_bar:
            active_pivs.extend(pivots_by_confirm_bar[i])

        if np.isnan(range_high[i]):
            continue

        for p in active_pivs:
            if not p["active"]:
                continue

            vol_ok = True
            if require_volume and avg_vol is not None and not np.isnan(avg_vol[i]):
                vol_ok = volumes[i] >= avg_vol[i] * volume_threshold

            if (highs[i] > p["level"] and closes[i] < p["level"] and p["count"] <= max_attempts
                    and highs[i] >= range_high[i] and vol_ok):
                events.append({
                    "kind": "upthrust",
                    "pivot_idx": p["pivot_idx"],
                    "pivot_level": p["level"],
                    "confirm_idx": i,
                    "attempts_used": p["count"],
                })
                p["active"] = False
            elif highs[i] > p["level"]:
                p["count"] += 1

        active_pivs = [p for p in active_pivs if p["active"]]

    return events


"""
=== ASL PINE SCRIPT (QuantVue, Mozilla Public License 2.0) - faqat ma'lumot uchun ===

// This Pine Script (TM) code is subject to the terms of the Mozilla Public
// License 2.0 at https://mozilla.org/MPL/2.0/
// (c) QuantVue

//@version=5
indicator("Wyckoff Springs [QuantVue]", overlay=true, shorttitle='Wyckoff Springs [QuantVue]')

type piv
    float p
    int b
    bool act = true
    int c = 0

pivlen      = input.int(6, 'Pivot Length')
reqVol      = input.bool(false, 'Require Volume Confirmation')
volThresh   = input.float(1.5, 'Volume Threshold')
rangePeriod = input.int(20, 'Trading Range Period')

var pivs    = array.new<piv>()
pl          = ta.pivotlow(pivlen, pivlen)
avgVol      = ta.sma(volume, rangePeriod)
meetsThresh = volume >= avgVol * volThresh
highRange   = ta.highest(rangePeriod)
lowRange    = ta.lowest(rangePeriod)

if not na(pl)
    pivs.unshift(piv.new(low[pivlen], bar_index[pivlen]))

for p in pivs
    if p.act
        if low < p.p and close > p.p and p.c <= 3 and low <= lowRange and (reqVol ? meetsThresh : true)
            label.new(bar_index, low, '', yloc=yloc.belowbar, style=label.style_label_up,
              color=color.lime, textcolor=color.white)
            p.act := false
            alert('Spring Detected', alert.freq_once_per_bar_close)
        else if low < p.p
            p.c += 1

plot(lowRange, 'Range Low', color=color.red)
"""
