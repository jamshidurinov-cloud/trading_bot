"""
luxalgo_range_detector.py
--------------------------
LuxAlgo "Range Detector" (Pine Script v5) indikatorining ANIQ, bar-by-bar
Python portlanishi.

Manba: LuxAlgo, CC BY-NC-SA 4.0 litsenziyasi ostida.
© LuxAlgo — asl Pine kodi ushbu faylning oxirida, izoh sifatida saqlangan.

MUHIM: bu — boshqa LuxAlgo fayllari (luxalgo_smc.py,
luxalgo_liquidity_sweeps.py) bilan bir xil qoidaga bo'ysunadi — bu fayl
"lib" (kutubxona), aniq portlangan, o'zgartirilmaydi. Moslashtirish
(masalan boshqa signal turlariga ulash) — ALOHIDA faylda bo'ladi.

Pine mantig'i (asl kod bo'yicha, qadam-baqadam):
1. Har bir bar uchun: ma = SMA(close, length), atr = ATR(atr_len) * mult
2. "count" — oxirgi `length` ta close'dan nechtasi JORIY ma'dan `atr`dan
   ko'proq uzoqlashgani (DIQQAT: solishtirish har doim JORIY ma/atr bilan,
   har bir o'tmishdagi barning o'zining ma/atr'i bilan EMAS - bu Pine
   kodining o'ziga xos xususiyati, portlashda ATAYLAB saqlangan).
3. Agar count==0 va oldingi barda count!=0 bo'lsa (yoki bu birinchi
   baholanadigan bar bo'lsa) - bu "range boshlandi" (fresh trigger):
   - Agar oldingi box bilan vaqt bo'yicha ustma-ust tushsa (n-length <=
     oldingi box'ning o'ng cheti) - box KENGAYTIRILADI (top/bottom max/min
     bilan yangilanadi, chap chegara O'ZGARMAYDI)
   - Aks holda - YANGI box yaratiladi (chap=n-length, o'ng=n,
     top=ma+atr, bottom=ma-atr)
4. Agar count==0 lekin fresh trigger emas (range DAVOM etyapti) - faqat
   box'ning O'NG CHEGARASI joriy barga cho'ziladi, top/bottom O'ZGARMAYDI.
5. Agar count!=0 - box holati UMUMAN o'zgarmaydi (na kengaymaydi, na
   yangilanadi) - keyingi fresh trigger'gacha "muzlab" qoladi.
6. HAR BIR barda (count'dan qat'iy nazar, agar box mavjud bo'lsa):
   agar close > box.top -> os=1 (yuqoriga buzilgan)
   agar close < box.bottom -> os=-1 (pastga buzilgan)
   (aks holda os o'zgarmaydi - oxirgi holatini saqlaydi)

MUHIM CHEKLOV (portlashda soddalashtirilgan joy): Pine'da `ta.sma`/`ta.atr`
yetarli tarixiy ma'lumot bo'lmaganda `na` qaytaradi, va `na` bilan solishtirish
Pine'da o'ziga xos qoidalarga bo'ysunadi. Bu portlashda, agar `ma` yoki `atr`
hali hisoblanmagan bo'lsa (yetarli bar yo'q), bar oddiygina O'TKAZIB
YUBORILADI (baholanmaydi) - bu, isinish davridan tashqari, natijaga
ta'sir qilmaydi.

MUHIM OGOHLANTIRISH (Jamshidga alohida aytiladi): standart `atr_len=500`
- bu juda uzoq tarix talab qiladi. Agar chaqiruvchi atigi ~144-300 ta
sham bersa, ATR yetarlicha "isinmagan" (yaqinlashmagan) bo'lishi mumkin -
bu funksiya haqiqiy ishlatishdan oldin, yetarli tarix (kamida atr_len,
afzalroq 2-3x atr_len) berilishi kerak.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def _rma(series: pd.Series, length: int) -> pd.Series:
    """Pine'ning ta.rma (Wilder's smoothing) bilan bir xil: alpha=1/length,
    boshidan (adjust=False) EMA."""
    return series.ewm(alpha=1.0 / length, adjust=False).mean()


def detect_luxalgo_range(df: pd.DataFrame, length: int = 20, mult: float = 1.0,
                           atr_len: int = 500) -> list:
    """LuxAlgo Range Detector'ni bar-by-bar simulyatsiya qiladi.

    Qaytaradi: har bir bar uchun holat lug'ati (list of dict), quyidagi
    maydonlar bilan:
        idx        - bar pozitsiyasi (df ichida, 0-indeksli)
        count      - shu bardagi "count" qiymati (yoki isinish davrida None)
        box_left, box_right - joriy box chegaralari (bar indeksi sifatida)
        box_top, box_bottom - joriy box narx chegaralari
        os         - -1 (pastga buzilgan), 0 (buzilmagan), 1 (yuqoriga buzilgan)
        fresh_trigger - shu barda YANGI yoki KENGAYTIRILGAN box paydo bo'ldimi

    Bu - xom, bar-by-bar holat. Moslashtiruvchi (masalan faqat "range
    boshlandi" yoki "range buzildi" hodisalarini ajratib oluvchi) mantiq
    ALOHIDA, boshqa faylda bo'lishi kerak (bu faylga qo'shilmaydi).
    """
    n_bars = len(df)
    close = df["close"].to_numpy(dtype=float)

    ma = df["close"].rolling(length).mean().to_numpy(dtype=float)
    tr = _true_range(df)
    atr = (_rma(tr, atr_len) * mult).to_numpy(dtype=float)

    box_top = None
    box_bottom = None
    box_left = None
    box_right = None
    os = 0
    prev_count = None  # None => Pine'dagi 'na' holati (birinchi baholash)

    results = []

    for i in range(n_bars):
        if i < length - 1 or np.isnan(ma[i]) or np.isnan(atr[i]):
            results.append({
                "idx": i, "count": None,
                "box_left": box_left, "box_right": box_right,
                "box_top": box_top, "box_bottom": box_bottom,
                "os": os, "fresh_trigger": False,
            })
            continue

        count = 0
        for k in range(length):
            j = i - k
            if j < 0:
                break
            if abs(close[j] - ma[i]) > atr[i]:
                count += 1

        # Pine: `count[1] != count` - agar count[1] (oldingi) `na` bo'lsa,
        # Pine'da `na != son` odatda TRUE beradi - shuning uchun birinchi
        # baholanadigan bar ham "fresh trigger" bo'lishi mumkin (agar count==0).
        is_fresh = (count == 0) and (prev_count is None or prev_count != 0)

        if is_fresh:
            left_idx = max(0, i - length)
            if box_right is not None and left_idx <= box_right:
                # Ustma-ust tushish -> box kengaytiriladi (chap chegara o'zgarmaydi)
                new_top = max(ma[i] + atr[i], box_top)
                new_bottom = min(ma[i] - atr[i], box_bottom)
                box_top = new_top
                box_bottom = new_bottom
                box_right = i
            else:
                # Yangi, alohida box
                box_top = ma[i] + atr[i]
                box_bottom = ma[i] - atr[i]
                box_left = left_idx
                box_right = i
                os = 0
        elif count == 0:
            # Range davom etyapti - faqat o'ng chegara cho'ziladi
            if box_right is not None:
                box_right = i
        # count != 0 -> box holati UMUMAN o'zgarmaydi (Pine'dagi kabi)

        if box_top is not None:
            if close[i] > box_top:
                os = 1
            elif close[i] < box_bottom:
                os = -1

        results.append({
            "idx": i, "count": count,
            "box_left": box_left, "box_right": box_right,
            "box_top": box_top, "box_bottom": box_bottom,
            "os": os, "fresh_trigger": is_fresh,
        })
        prev_count = count

    return results


"""
=== ASL PINE SCRIPT (LuxAlgo, CC BY-NC-SA 4.0) - faqat ma'lumot uchun ===

// This work is licensed under a Attribution-NonCommercial-ShareAlike 4.0
// International (CC BY-NC-SA 4.0) https://creativecommons.org/licenses/by-nc-sa/4.0/
// (c) LuxAlgo

//@version=5
indicator("Range Detector [LuxAlgo]", "LuxAlgo - Range Detector", overlay=true,
  max_boxes_count=500, max_lines_count=500)

length = input.int(20, 'Minimum Range Length', minval=2)
mult   = input.float(1., 'Range Width', minval=0, step=0.1)
atrLen = input.int(500, 'ATR Length', minval=1)

var box bx = na
var line lvl = na
var float max = na
var float min = na
var os = 0

n = bar_index
atr = ta.atr(atrLen) * mult
ma = ta.sma(close, length)

count = 0
for i = 0 to length-1
    count += math.abs(close[i] - ma) > atr ? 1 : 0

if count == 0 and count[1] != count
    if n[length] <= bx.get_right()
        max := math.max(ma + atr, bx.get_top())
        min := math.min(ma - atr, bx.get_bottom())
        bx.set_top(max)
        bx.set_rightbottom(n, min)
    else
        max := ma + atr
        min := ma - atr
        bx := box.new(n[length], ma + atr, n, ma - atr, na)
        os := 0
else if count == 0
    bx.set_right(n)

if close > bx.get_top()
    os := 1
else if close < bx.get_bottom()
    os := -1
"""
