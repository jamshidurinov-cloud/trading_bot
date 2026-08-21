"""
jackpot_signal.py

Moslashuvchan (istalgan uzunlikdagi) range aniqlash, Spring/Upthrust va
"JACKPOT" (Spring/Upthrust + Test) signal mantiqi.

Bu fayl main.py'dan mustaqil — faqat pandas DataFrame (OHLC ustunlari bilan)
qabul qiladi, hech qanday tashqi API yoki Telegram bilan ishlamaydi. Shu
sababli, alohida sinash/sozlash oson: bu faylni o'zgartirib, natijasini
sinab ko'rish uchun main.py yoki botni ishga tushirish shart emas.

main.py bu fayldan quyidagilarni import qiladi:
    from jackpot_signal import (
        RANGE_TOLERANCE_PCT, CONFIRM_CANDLES, TEST_TOLERANCE_PCT, TEST_SEARCH_WINDOW,
        find_swing_points, cluster_equal_levels, detect_dynamic_range,
        detect_dynamic_spring_upthrust, detect_jackpot_signal,
    )
"""

# ============================================================================
# SOZLAMALAR
# ============================================================================

RANGE_TOLERANCE_PCT = 0.3   # "teng" cho'qqi/tub deb hisoblash uchun ruxsat etilgan farq (%)
CONFIRM_CANDLES = 2         # range'ga qaytgandan keyin tasdiqlash uchun kutiladigan sveчalar soni
TEST_TOLERANCE_PCT = 0.15   # "test" darajaga qanchalik yaqin kelishi kerak
TEST_SEARCH_WINDOW = 30     # test candidatidan oldin, sweep voqeasini qidirish oynasi


# ============================================================================
# SWING NUQTALARI
# ============================================================================

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


# ============================================================================
# MOSLASHUVCHAN RANGE ("TENG CHO'QQI/TUB" ASOSIDA)
# ============================================================================

def cluster_equal_levels(points, tolerance_pct, min_span=10, mode="high"):
    """points: [(indeks, narx), ...]. Bir-biriga tolerance_pct ichida yaqin narxlarni
    guruhlaydi ("teng cho'qqi/tub"). Faqat vaqt bo'yicha yetarlicha uzoq tarqalgan
    (min_span sveчadan ko'p) guruhlarni qaytaradi — bu tasodifiy, yaqin-orada
    joylashgan shovqinni chinakam qayta-qayta sinalgan darajadan ajratadi.

    Chegara sifatida GURUHDAGI ENG EKSTREMAL nuqta olinadi (o'rtacha emas):
    mode='high' -> guruhdagi eng YUQORI narx (haqiqiy sinalgan resistance)
    mode='low'  -> guruhdagi eng PAST narx (haqiqiy sinalgan support)
    """
    points = sorted(points, key=lambda p: p[1])
    clusters = []
    used = [False] * len(points)

    for i in range(len(points)):
        if used[i]:
            continue
        group = [points[i]]
        used[i] = True
        for j in range(i + 1, len(points)):
            if used[j]:
                continue
            avg_so_far = sum(v for _, v in group) / len(group)
            if avg_so_far == 0:
                continue
            if abs(points[j][1] - avg_so_far) / avg_so_far * 100 <= tolerance_pct:
                group.append(points[j])
                used[j] = True
        indices = [idx for idx, _ in group]
        if len(group) >= 2 and (max(indices) - min(indices)) >= min_span:
            values = [v for _, v in group]
            level = max(values) if mode == "high" else min(values)
            clusters.append({"level": level, "indices": indices})
    return clusters


def detect_dynamic_range(df, swing_window=6, lookback=144, tolerance_pct=RANGE_TOLERANCE_PCT):
    """Qattiq sveчa soniga bog'lanmasdan, 'teng cho'qqilar' va 'teng tublar' asosida
    range chegaralarini topadi — range necha sveчa davom etgani muhim emas."""
    if len(df) < lookback:
        return None

    sub = df.iloc[-lookback:]
    highs = sub["high"].values
    lows = sub["low"].values

    swing_high_idx, swing_low_idx = find_swing_points(highs, lows, window=swing_window, exclude_last=True)
    if not swing_high_idx or not swing_low_idx:
        return None

    high_clusters = cluster_equal_levels([(i, highs[i]) for i in swing_high_idx], tolerance_pct, mode="high")
    low_clusters = cluster_equal_levels([(i, lows[i]) for i in swing_low_idx], tolerance_pct, mode="low")
    if not high_clusters or not low_clusters:
        return None

    # Eng so'nggi (joriy vaqtga eng yaqin) cluster'larni tanlaymiz - hozirgi range shu
    best_high = max(high_clusters, key=lambda c: max(c["indices"]))
    best_low = max(low_clusters, key=lambda c: max(c["indices"]))

    range_high = best_high["level"]
    range_low = best_low["level"]
    if range_high <= range_low:
        return None

    return {"range_high": range_high, "range_low": range_low}


# ============================================================================
# ODDIY SPRING / UPTHRUST (moslashuvchan range, davomiylik bilan tasdiqlangan)
# ============================================================================

def detect_dynamic_spring_upthrust(df, swing_window=6, lookback=144,
                                     tolerance_pct=RANGE_TOLERANCE_PCT, confirm_candles=CONFIRM_CANDLES):
    """Moslashuvchan range'ga sweep qilib qaytgandan keyin, CONFIRM_CANDLES ta sveчa
    davomida narx shu yo'nalishda davom etsa, signal beradi. Har voqea faqat bir
    marta xabar qilinadi."""
    range_info = detect_dynamic_range(df, swing_window, lookback, tolerance_pct)
    if range_info is None:
        return None

    sub = df.iloc[-lookback:]
    highs = sub["high"].values
    lows = sub["low"].values
    closes = sub["close"].values
    times = sub.index
    n = len(sub)

    range_high = range_info["range_high"]
    range_low = range_info["range_low"]

    event_idx = n - 1 - confirm_candles
    if event_idx < swing_window:
        return None

    # SPRING: event_idx svechada past nuqta range_low'dan pastga tushib, yopilish qaytgan,
    # so'ngra keyingi confirm_candles ta sveчa davomida narx pasaymagan (davom etgan)
    if lows[event_idx] < range_low and closes[event_idx] > range_low:
        entry_close = closes[event_idx]
        confirmed = all(closes[event_idx + k] >= entry_close for k in range(1, confirm_candles + 1))
        if confirmed and closes[-1] > entry_close:
            return {
                "type": "dynamic_spring",
                "range_high": range_high,
                "range_low": range_low,
                "event_time": str(times[event_idx]),
                "event_low": lows[event_idx],
                "event_close": entry_close,
                "current_close": closes[-1],
            }

    # UPTHRUST: teskarisi
    if highs[event_idx] > range_high and closes[event_idx] < range_high:
        entry_close = closes[event_idx]
        confirmed = all(closes[event_idx + k] <= entry_close for k in range(1, confirm_candles + 1))
        if confirmed and closes[-1] < entry_close:
            return {
                "type": "dynamic_upthrust",
                "range_high": range_high,
                "range_low": range_low,
                "event_time": str(times[event_idx]),
                "event_high": highs[event_idx],
                "event_close": entry_close,
                "current_close": closes[-1],
            }

    return None


# ============================================================================
# 🎰 JACKPOT — Spring/Upthrust + TEST (eng yuqori ishonchli signal)
# ============================================================================

def detect_jackpot_signal(df, swing_window=6, lookback=144, tolerance_pct=RANGE_TOLERANCE_PCT,
                            test_tolerance_pct=TEST_TOLERANCE_PCT, test_window=TEST_SEARCH_WINDOW,
                            confirm_candles=CONFIRM_CANDLES):
    """🎰 JACKPOT — eng yuqori ishonchli signal: klassik Wyckoff 'Spring + Test'
    (yoki 'Upthrust + Test') pattern'i:

    1. Range chegarasidan soxta chiqib qaytadi (sweep)
    2. Keyinroq narx o'sha darajaga QAYTIB KELIB, uni TEST QILADI (buzmasdan ushlab turadi)
    3. Test'dan keyin narx keskin TESKARI tomonga ketadi — signal shu yerda beriladi

    Bu — oddiy sweep+return'dan farqli, qo'shimcha 'test' bosqichi bilan tasdiqlangan,
    shuning uchun kamroq, lekin ancha ishonchliroq signal beradi."""
    range_info = detect_dynamic_range(df, swing_window, lookback, tolerance_pct)
    if range_info is None:
        return None

    sub = df.iloc[-lookback:]
    highs = sub["high"].values
    lows = sub["low"].values
    closes = sub["close"].values
    times = sub.index
    n = len(sub)

    range_high = range_info["range_high"]
    range_low = range_info["range_low"]

    test_idx = n - 1 - confirm_candles
    if test_idx < swing_window:
        return None

    search_start = max(swing_window, test_idx - test_window)

    # --- SPRING + TEST (bullish) ---
    if range_low > 0 and abs(lows[test_idx] - range_low) / range_low * 100 <= test_tolerance_pct \
            and closes[test_idx] > range_low:
        event_idx = None
        for k in range(search_start, test_idx):
            if lows[k] < range_low and closes[k] > range_low:
                event_idx = k  # eng so'nggi (test'ga eng yaqin) sweep voqeasi
        if event_idx is not None:
            test_close = closes[test_idx]
            confirmed = all(closes[test_idx + k] >= test_close for k in range(1, confirm_candles + 1))
            if confirmed and closes[-1] > test_close:
                return {
                    "type": "jackpot_spring",
                    "range_high": range_high,
                    "range_low": range_low,
                    "event_time": str(times[event_idx]),
                    "event_low": lows[event_idx],
                    "test_time": str(times[test_idx]),
                    "test_low": lows[test_idx],
                    "current_close": closes[-1],
                }

    # --- UPTHRUST + TEST (bearish) ---
    if range_high > 0 and abs(highs[test_idx] - range_high) / range_high * 100 <= test_tolerance_pct \
            and closes[test_idx] < range_high:
        event_idx = None
        for k in range(search_start, test_idx):
            if highs[k] > range_high and closes[k] < range_high:
                event_idx = k
        if event_idx is not None:
            test_close = closes[test_idx]
            confirmed = all(closes[test_idx + k] <= test_close for k in range(1, confirm_candles + 1))
            if confirmed and closes[-1] < test_close:
                return {
                    "type": "jackpot_upthrust",
                    "range_high": range_high,
                    "range_low": range_low,
                    "event_time": str(times[event_idx]),
                    "event_high": highs[event_idx],
                    "test_time": str(times[test_idx]),
                    "test_high": highs[test_idx],
                    "current_close": closes[-1],
                }

    return None
