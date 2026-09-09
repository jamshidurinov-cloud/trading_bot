"""
luxalgo_smc.py

LuxAlgo "Smart Money Concepts [LuxAlgo]" Pine Script indikatorining TO'LIQ,
ANIQ Python porti. Manba: foydalanuvchi tomonidan TradingView Pine Editor'idan
to'g'ridan-to'g'ri nusxalab olingan, to'liq (@version=5) kod.

Litsenziya: CC BY-NC-SA 4.0 (Attribution-NonCommercial-ShareAlike) - © LuxAlgo
DIQQAT: bu litsenziya NonCommercial (notijorat) shartini o'z ichiga oladi.

Bu fayl - avvalgi smc_lib.py (smartmoneyconcepts kutubxonasi, mustaqil
qayta yozilgan, LuxAlgo'nikidan FARQLI algoritm) o'rniga, ENDI LuxAlgo'ning
O'ZINING aniq mexanizmini takrorlaydi:

1. SWING/LEG: markazlashgan oyna emas, balki "leg" (oyoq) mexanizmi -
   har bar uchun ta.highest/ta.lowest(size) orqali hisoblanadi, `size` bar
   confirmatsiya bilan.
2. BOS/CHoCH: 4-nuqta naqsh solishtirish EMAS - balki bitta saqlanadigan
   "trend holati" + bitta faol swingHigh va bitta faol swingLow pivot.
   Narx pivot darajasini kesib o'tganda, joriy trend holatiga qarab
   BOS yoki CHoCH deb belgilanadi.
3. FVG: moslashuvchan "Auto Threshold" (butun tarixning o'rtacha foizli
   svecha harakati asosida), va MUHIM - o'rta svechaning YOPILISHI ham
   darajadan o'tishi shart (bizning eski kodimizda bu yo'q edi).

HALI BOTGA ULANMAGAN - main.py hech narsani bu fayldan import qilmaydi.
"""

import numpy as np
import pandas as pd

BULLISH_LEG = 1
BEARISH_LEG = 0
BULLISH = 1
BEARISH = -1


def compute_legs(highs, lows, size):
    """Pine: leg(size) funksiyasining Python porti.
    `var leg = 0` - bar-baqadam saqlanadigan holat.
    newLegHigh = high[size] > ta.highest(size)   (joriy `size` barning maksimumi)
    newLegLow  = low[size]  < ta.lowest(size)
    Natija: har bar uchun joriy "leg" qiymati (0=bearish, 1=bullish) massivi."""
    n = len(highs)
    legs = np.zeros(n, dtype=np.int8)
    cur_leg = 0
    for i in range(n):
        if i - size < 0:
            legs[i] = cur_leg
            continue
        window_start = max(0, i - size + 1)
        highest = highs[window_start:i + 1].max()
        lowest = lows[window_start:i + 1].min()
        high_size_ago = highs[i - size]
        low_size_ago = lows[i - size]

        new_leg_high = high_size_ago > highest
        new_leg_low = low_size_ago < lowest

        if new_leg_high:
            cur_leg = BEARISH_LEG
        elif new_leg_low:
            cur_leg = BULLISH_LEG
        legs[i] = cur_leg
    return legs


def detect_pivots(df, size):
    """Pine: getCurrentStructure(size) funksiyasining asosiy qismi (faqat
    swingHigh/swingLow pivot yozib borish, EQH/EQL va label chizish HAZircha
    kiritilmagan - ular alohida funksiyada).

    Qaytaradi: pivots ro'yxati, har biri
      {kind: 'high'/'low', level: narx, confirm_idx: qachon tasdiqlangan,
       source_idx: haqiqiy svecha indeksi (confirm_idx - size)}
    vaqt bo'yicha tartiblangan."""
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    legs = compute_legs(highs, lows, size)

    pivots = []
    for i in range(1, n):
        if legs[i] == legs[i - 1]:
            continue  # newPivot faqat leg ozgarganda
        source_idx = i - size
        if source_idx < 0:
            continue
        if legs[i] == BULLISH_LEG:
            # startOfBullishLeg -> pivotLow -> low[size] darajasi yoziladi
            pivots.append({
                "kind": "low",
                "level": float(lows[source_idx]),
                "confirm_idx": i,
                "source_idx": source_idx,
            })
        else:
            # startOfBearishLeg -> pivotHigh -> high[size] darajasi yoziladi
            pivots.append({
                "kind": "high",
                "level": float(highs[source_idx]),
                "confirm_idx": i,
                "source_idx": source_idx,
            })
    return pivots


def detect_bos_choch(df, size=50):
    """Pine: displayStructure() funksiyasining asosiy mantig'i.

    Bitta faol swingHigh va bitta faol swingLow pivot saqlanadi (yangi pivot
    kelganda almashtiriladi, 'crossed' holati False'ga qaytariladi). Narx
    (yopilish) pivot darajasini kesib o'tganda:
      - agar swingHigh kesilsa (yuqoriga chiqsa): joriy trend BEARISH bo'lsa
        -> CHoCH, aks holda -> BOS. Keyin trend := BULLISH.
      - agar swingLow kesilsa (pastga tushsa): joriy trend BULLISH bo'lsa
        -> CHoCH, aks holda -> BOS. Keyin trend := BEARISH.

    Qaytaradi: events ro'yxati {kind: 'BOS'/'CHOCH', direction: 'bullish'/
    'bearish', level: narx, pivot_source_idx:, confirm_idx: (bu pivot qachon
    yozilgan), break_idx: (narx qachon kesib otgan)}."""
    closes = df["close"].values
    n = len(df)
    pivots = detect_pivots(df, size)

    # pivotlarni vaqt boyicha (confirm_idx) tartiblab, simulyatsiya qilamiz
    pivots_by_confirm = sorted(pivots, key=lambda p: p["confirm_idx"])

    active_high = None  # {'level':, 'crossed': False, 'source_idx':, 'confirm_idx':}
    active_low = None
    trend_bias = 0  # 0 = aniqlanmagan, keyin BULLISH/BEARISH
    events = []

    pivot_ptr = 0
    for i in range(n):
        # shu barda tasdiqlangan yangi pivotlarni faol qilib qoyamiz
        while pivot_ptr < len(pivots_by_confirm) and pivots_by_confirm[pivot_ptr]["confirm_idx"] == i:
            p = pivots_by_confirm[pivot_ptr]
            if p["kind"] == "high":
                active_high = {"level": p["level"], "crossed": False,
                               "source_idx": p["source_idx"], "confirm_idx": p["confirm_idx"]}
            else:
                active_low = {"level": p["level"], "crossed": False,
                              "source_idx": p["source_idx"], "confirm_idx": p["confirm_idx"]}
            pivot_ptr += 1

        # crossover(close, swingHigh.level) - joriy yopilish darajadan yuqori,
        # oldingi yopilish darajadan past yoki teng bolishi kerak (ta.crossover)
        if active_high is not None and not active_high["crossed"] and i > 0:
            if closes[i - 1] <= active_high["level"] < closes[i]:
                kind = "CHOCH" if trend_bias == BEARISH else "BOS"
                events.append({
                    "kind": kind, "direction": "bullish", "level": active_high["level"],
                    "pivot_source_idx": active_high["source_idx"],
                    "pivot_confirm_idx": active_high["confirm_idx"], "break_idx": i,
                })
                active_high["crossed"] = True
                trend_bias = BULLISH

        if active_low is not None and not active_low["crossed"] and i > 0:
            if closes[i - 1] >= active_low["level"] > closes[i]:
                kind = "CHOCH" if trend_bias == BULLISH else "BOS"
                events.append({
                    "kind": kind, "direction": "bearish", "level": active_low["level"],
                    "pivot_source_idx": active_low["source_idx"],
                    "pivot_confirm_idx": active_low["confirm_idx"], "break_idx": i,
                })
                active_low["crossed"] = True
                trend_bias = BEARISH

    return events


def detect_fvg(df, auto_threshold=True, fixed_threshold=0.0):
    """Pine: drawFairValueGaps() funksiyasining porti (bir xil timeframe uchun,
    MTF/request.security qismisiz - bizga hozircha kerak emas).

    3 svechalik oyna: candle[i-2] (birinchi), candle[i-1] (impuls),
    candle[i] (joriy). MUHIM: bizning eski kodimizdan farqli, bu yerda
    IMPULS SVECHANING YOPILISHI ham darajadan o'tishi SHART, va chegara
    (threshold) butun tarixning o'rtacha foizli harakati asosida
    moslashuvchan hisoblanadi (auto_threshold=True bo'lsa).

    Qaytaradi: fvg hodisalari ro'yxati {direction:, top:, bottom:,
    impulse_idx: (i-1), confirm_idx: (i, FVG shu bar aniqlanadi)}."""
    opens = df["open"].values
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)

    # barDeltaPercent = (close[i-1]-open[i-1]) / (open[i-1]*100) - PINE FORMULASI ANIQ SHUNDAY
    # (100 ga bolinishi "foiz" emas, shunchaki Pine kodidagi aniq formula)
    bar_delta_percent = np.zeros(n)
    for i in range(1, n):
        if opens[i] != 0:
            bar_delta_percent[i] = (closes[i] - opens[i]) / (opens[i] * 100)

    # threshold = cum(abs(barDeltaPercent)) / bar_index * 2  - kumulyativ ortacha
    abs_cum = np.cumsum(np.abs(bar_delta_percent))

    events = []
    for i in range(2, n):
        if auto_threshold:
            # bar_index - Pine'da 0'dan boshlanadi, biz ham i ni ishlatamiz
            threshold = (abs_cum[i - 1] / i) * 2 if i > 0 else 0.0
        else:
            threshold = fixed_threshold

        last_close = closes[i - 1]
        last_open = opens[i - 1]
        current_high = highs[i]
        current_low = lows[i]
        last2_high = highs[i - 2]
        last2_low = lows[i - 2]
        delta = bar_delta_percent[i - 1]

        bullish_fvg = (current_low > last2_high) and (last_close > last2_high) and (delta > threshold)
        bearish_fvg = (current_high < last2_low) and (last_close < last2_low) and (-delta > threshold)

        if bullish_fvg:
            events.append({
                "direction": "bullish", "top": float(current_low), "bottom": float(last2_high),
                "impulse_idx": i - 1, "confirm_idx": i,
            })
        if bearish_fvg:
            events.append({
                "direction": "bearish", "top": float(last2_low), "bottom": float(current_high),
                "impulse_idx": i - 1, "confirm_idx": i,
            })

    # Pine: deleteFairValueGaps() - narx qaytib FVG zonasiga kirsa, "toldirilgan"
    # (mitigatsiya qilingan) deb belgilanadi. Har FVG uchun, hosil bolgandan
    # keyingi birinchi shunday svechani topamiz.
    for ev in events:
        ev["mitigated_idx"] = None
        for i in range(ev["confirm_idx"] + 1, n):
            if ev["direction"] == "bullish" and lows[i] < ev["bottom"]:
                ev["mitigated_idx"] = i
                break
            if ev["direction"] == "bearish" and highs[i] > ev["top"]:
                ev["mitigated_idx"] = i
                break

    return events


def _compute_atr(highs, lows, closes, period=200):
    """Pine ta.atr(200) - RMA (Wilder) silliqlashtirilgan True Range o'rtachasi."""
    n = len(highs)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = np.zeros(n)
    atr[0] = tr[0]
    alpha = 1.0 / period
    for i in range(1, n):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i - 1]
    return atr


def compute_parsed_high_low(df, filter_method="atr"):
    """Pine: highVolatilityBar filtri. Juda "portlagan" (baland-past farqi
    2x o'rtacha tebranishdan katta) svechalarda, OB uchun high/low ORNI
    ALMASHTIRILADI - bu haddan tashqari tebranuvchi svechalarni OB manbai
    sifatida tanlanishdan saqlaydi.

    filter_method: "atr" (ta.atr(200)) yoki "range" (kumulyativ ortacha TR)."""
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    n = len(df)

    if filter_method == "atr":
        volatility = _compute_atr(highs, lows, closes, period=200)
    else:
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        cum_tr = np.cumsum(tr)
        volatility = np.array([cum_tr[i] / (i + 1) if i >= 0 else 0 for i in range(n)])

    high_volatility_bar = (highs - lows) >= (2 * volatility)
    parsed_high = np.where(high_volatility_bar, lows, highs)
    parsed_low = np.where(high_volatility_bar, highs, lows)
    return parsed_high, parsed_low


def detect_order_blocks(df, bos_choch_events, filter_method="atr", mitigation="highlow"):
    """Pine: storeOrdeBlock() + deleteOrderBlocks() portlari.

    Har bir BOS/CHoCH voqeasida, pivot manba nuqtasidan (source_idx) sinish
    nuqtasigacha (break_idx) bo'lgan oraliqda, parsedHigh/Low massividan
    "eng ekstremal" svechani (bullish uchun eng past parsedLow, bearish
    uchun eng baland parsedHigh) Order Block manbai sifatida tanlaydi.

    mitigation: "close" yoki "highlow" (standart, Pine'dagi default HIGHLOW).

    Qaytaradi: order_blocks ro'yxati {direction:, bar_high:, bar_low:,
    source_idx: (OB svechasi), created_idx: (BOS/CHoCH sinish nuqtasi),
    mitigated_idx: (agar mitigatsiya bo'lgan bo'lsa, aks holda None)}."""
    parsed_high, parsed_low = compute_parsed_high_low(df, filter_method=filter_method)
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    n = len(df)

    order_blocks = []
    for ev in bos_choch_events:
        start = ev["pivot_source_idx"]
        end = ev["break_idx"]
        if start >= end or start < 0:
            continue
        if ev["direction"] == "bullish":
            window = parsed_low[start:end + 1]
            rel_idx = int(np.argmin(window))
        else:
            window = parsed_high[start:end + 1]
            rel_idx = int(np.argmax(window))
        ob_idx = start + rel_idx

        order_blocks.append({
            "direction": ev["direction"],
            "bar_high": float(highs[ob_idx]),
            "bar_low": float(lows[ob_idx]),
            "source_idx": ob_idx,
            "created_idx": end,
            "mitigated_idx": None,
        })

    # Mitigatsiya (invalidatsiya) - har OB uchun, yaratilgandan keyingi
    # birinchi svecha qachon uni "buzganini" topamiz
    bearish_source = closes if mitigation == "close" else highs
    bullish_source = closes if mitigation == "close" else lows

    for ob in order_blocks:
        for i in range(ob["created_idx"] + 1, n):
            if ob["direction"] == "bearish" and bearish_source[i] > ob["bar_high"]:
                ob["mitigated_idx"] = i
                break
            if ob["direction"] == "bullish" and bullish_source[i] < ob["bar_low"]:
                ob["mitigated_idx"] = i
                break

    return order_blocks


def detect_equal_highs_lows(df, size=3, threshold=0.1):
    """Pine: getCurrentStructure(size, equalHighLow=true) qismi.

    Har safar yangi (kichik oynali, standart 3 svechali) pivot tasdiqlanganda,
    uning darajasi OLDINGI xuddi shu turdagi (yuqori/past) pivot darajasi
    bilan solishtiriladi. Agar farq (threshold * ATR(200))dan kichik bo'lsa -
    "teng cho'qqi/tub" (EQH/EQL) deb belgilanadi.

    Qaytaradi: events ro'yxati {kind: 'EQH'/'EQL', level1:, level2:,
    idx1:, idx2: (ikkinchi, tasdiqlangan nuqta)}."""
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    n = len(df)
    atr = _compute_atr(highs, lows, closes, period=200)
    pivots = detect_pivots(df, size)
    pivots_sorted = sorted(pivots, key=lambda p: p["confirm_idx"])

    events = []
    last_high_pivot = None
    last_low_pivot = None
    for p in pivots_sorted:
        if p["kind"] == "high":
            if last_high_pivot is not None:
                idx = p["confirm_idx"]
                if abs(last_high_pivot["level"] - p["level"]) < threshold * atr[idx]:
                    events.append({
                        "kind": "EQH", "level1": last_high_pivot["level"], "level2": p["level"],
                        "idx1": last_high_pivot["source_idx"], "idx2": p["source_idx"],
                        "confirm_idx": idx,
                    })
            last_high_pivot = p
        else:
            if last_low_pivot is not None:
                idx = p["confirm_idx"]
                if abs(last_low_pivot["level"] - p["level"]) < threshold * atr[idx]:
                    events.append({
                        "kind": "EQL", "level1": last_low_pivot["level"], "level2": p["level"],
                        "idx1": last_low_pivot["source_idx"], "idx2": p["source_idx"],
                        "confirm_idx": idx,
                    })
            last_low_pivot = p

    return events


def compute_premium_discount_zones(df):
    """Pine: updateTrailingExtremes() + drawPremiumDiscountZones() porti.

    trailing.top/bottom - chart boshidan (yoki berilgan df boshidan) hozirgi
    barga qadar kuzatilgan eng yuqori high va eng past low (doimiy yangilanib
    boruvchi, kengayuvchi oyna).

    Qaytaradi: DataFrame - har bar uchun top, bottom, premium_top,
    premium_bottom, discount_top, discount_bottom, equilibrium_top,
    equilibrium_bottom ustunlari bilan."""
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)

    top = np.zeros(n)
    bottom = np.zeros(n)
    top[0] = highs[0]
    bottom[0] = lows[0]
    for i in range(1, n):
        top[i] = max(highs[i], top[i - 1])
        bottom[i] = min(lows[i], bottom[i - 1])

    result = pd.DataFrame({
        "top": top,
        "bottom": bottom,
        "premium_bottom": 0.95 * top + 0.05 * bottom,
        "premium_top": top,
        "discount_bottom": bottom,
        "discount_top": 0.95 * bottom + 0.05 * top,
        "equilibrium_bottom": 0.525 * bottom + 0.475 * top,
        "equilibrium_top": 0.525 * top + 0.475 * bottom,
    }, index=df.index)
    return result


def compute_mtf_levels(df, period="D"):
    """Pine: drawLevels() porti (request.security orqali oldingi davr
    yuqori/past narxini olish) - Python'da pandas resample orqali.

    period: "D" (kunlik), "W" (haftalik), yoki "ME" (oylik).

    Qaytaradi: har bar uchun "oldingi to'liq davr"ning yuqori/past narxini
    ko'rsatuvchi Series juftligi (prev_high, prev_low) - bular PDH/PDL,
    PWH/PWL, yoki PMH/PML kabi darajalarga mos keladi."""
    resampled = df.resample(period).agg({"high": "max", "low": "min"})
    prev_high = resampled["high"].shift(1)
    prev_low = resampled["low"].shift(1)

    # har bir asl svechaga, oz davriga tegishli "oldingi davr" qiymatini
    # moslashtirib qoyamiz (forward-fill orqali)
    period_index = df.index.to_period(period).to_timestamp()
    prev_high_series = prev_high.reindex(period_index, method="ffill")
    prev_low_series = prev_low.reindex(period_index, method="ffill")
    prev_high_series.index = df.index
    prev_low_series.index = df.index
    return prev_high_series, prev_low_series
