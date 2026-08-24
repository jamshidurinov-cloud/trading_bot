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
PROMINENCE_WINDOW = 40      # Sweep uchun: "ajralib turgan" darajani aniqlash oynasi
PROMINENCE_MIN_HISTORY = 10  # ishonchli referens uchun kamida shuncha oldingi sveчa kerak
BOS_SWING_WINDOW = 6        # BOS uchun: eng yaqin tasdiqlangan swing nuqta oynasi


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


def is_level_respected(highs, lows, lo_idx, hi_idx, level, tolerance_pct, mode):
    """Ikki nuqta orasidagi BARCHA sveчalarni tekshiradi — agar ulardan birortasi
    ham chegarani (tolerantlikdan tashqari) buzgan bo'lsa, bu range 'haqiqiy emas'
    deb hisoblanadi (ya'ni ikki nuqta orasida narx allaqachon chegaradan chiqib
    ketgan bo'lsa, ular 'range chegarasi' bo'la olmaydi)."""
    if level == 0:
        return False
    segment = highs[lo_idx:hi_idx + 1] if mode == "high" else lows[lo_idx:hi_idx + 1]
    if mode == "high":
        extreme = segment.max()
        return (extreme - level) / level * 100 <= tolerance_pct
    extreme = segment.min()
    return (level - extreme) / level * 100 <= tolerance_pct


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

    # Eng so'nggidan boshlab, HAQIQATAN ushlab turilgan (orasida buzilmagan) cluster'ni qidiramiz
    best_high = None
    for c in sorted(high_clusters, key=lambda c: max(c["indices"]), reverse=True):
        lo_idx, hi_idx = min(c["indices"]), max(c["indices"])
        if is_level_respected(highs, lows, lo_idx, hi_idx, c["level"], tolerance_pct, "high"):
            best_high = c
            break
    if best_high is None:
        return None

    best_low = None
    for c in sorted(low_clusters, key=lambda c: max(c["indices"]), reverse=True):
        lo_idx, hi_idx = min(c["indices"]), max(c["indices"])
        if is_level_respected(highs, lows, lo_idx, hi_idx, c["level"], tolerance_pct, "low"):
            best_low = c
            break
    if best_low is None:
        return None

    range_high = best_high["level"]
    range_low = best_low["level"]
    if range_high <= range_low:
        return None

    range_start_idx = min(min(best_high["indices"]), min(best_low["indices"]))

    return {"range_high": range_high, "range_low": range_low, "range_start_idx": range_start_idx}


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

PRE_RANGE_LOOKBACK = 20      # range boshlanishidan oldin necha sveчa tekshiriladi
PRE_RANGE_MIN_MOVE_MULT = 1.0  # oldingi harakat range balandligining necha barobari bo'lishi kerak


def check_pre_range_movement(sub, range_start_idx, range_height,
                               lookback_candles=PRE_RANGE_LOOKBACK, min_move_mult=PRE_RANGE_MIN_MOVE_MULT):
    """Range paydo bo'lishidan OLDIN narx haqiqiy harakat qilganini tekshiradi
    (yo'nalishidan qat'iy nazar) — butunlay tekis, harakatsiz joydan chiqqan
    'range'larni rad etadi. Bu Wyckoff akkumulyatsiya/distribution'ning haqiqiy
    bo'lishi uchun muhim shart: range'dan oldin narx qayerdandir kelgan bo'lishi kerak."""
    if range_start_idx <= 0 or range_height <= 0:
        return True  # ma'lumot yetarli emas - xavfsiz tomonga, filtrlamaymiz

    start = max(0, range_start_idx - lookback_candles)
    pre_segment = sub.iloc[start:range_start_idx]
    if pre_segment.empty:
        return True

    pre_move = pre_segment["high"].max() - pre_segment["low"].min()
    return pre_move >= range_height * min_move_mult


def detect_jackpot_signal(df, swing_window=6, lookback=144, tolerance_pct=RANGE_TOLERANCE_PCT,
                            test_tolerance_pct=TEST_TOLERANCE_PCT, test_window=TEST_SEARCH_WINDOW,
                            confirm_candles=CONFIRM_CANDLES):
    """🎰 JACKPOT — eng yuqori ishonchli signal: klassik Wyckoff 'Spring + Test'
    (yoki 'Upthrust + Test') pattern'i:

    1. Range chegarasidan soxta chiqib qaytadi (sweep)
    2. Keyinroq narx o'sha darajaga QAYTIB KELIB, uni TEST QILADI (buzmasdan ushlab turadi)
    3. Test'dan keyin narx keskin TESKARI tomonga ketadi — signal shu yerda beriladi

    Bu — oddiy sweep+return'dan farqli, qo'shimcha 'test' bosqichi bilan tasdiqlangan,
    shuning uchun kamroq, lekin ancha ishonchliroq signal beradi.

    Qo'shimcha filtr: range paydo bo'lishidan oldin haqiqiy narx harakati bo'lgan
    bo'lishi kerak (tekis, harakatsiz joydan chiqqan range'lar rad etiladi)."""
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

    if not check_pre_range_movement(sub, range_info["range_start_idx"], range_high - range_low):
        return None

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


# ============================================================================
# OB/FVG RETRACEMENT ENTRY — BOS'dan keyin, OB yoki FVG zonasiga qaytishni kutadi
# ============================================================================

RETRACEMENT_WINDOW = 30   # BOS'dan keyin retracement uchun necha sveцha kutish
OB_SEARCH_BACK = 10       # Order Block'ni FVG'dan necha sveцha orqaga qarab qidirish


def find_order_block(closes, opens, start_idx, direction):
    """start_idx'dan orqaga qarab, 'direction'ga QARAMA-QARSHI rangdagi eng yaqin
    svechani Order Block sifatida topadi (bullish OB = bearish/qizil svecha,
    bearish OB = bullish/yashil svecha — kuchli harakatdan oldingi so'nggi
    'qarshi' svecha, bu yerda smart money order qoldirgan deb hisoblanadi)."""
    for i in range(start_idx, max(-1, start_idx - OB_SEARCH_BACK), -1):
        if direction == "bullish" and closes[i] < opens[i]:
            return i
        if direction == "bearish" and closes[i] > opens[i]:
            return i
    return None


def detect_ob_fvg_entry(df, lookback=144, min_fvg_mult=0.5, min_sweep_mult=0.15,
                          prominence_window=PROMINENCE_WINDOW, prominence_min_history=PROMINENCE_MIN_HISTORY,
                          bos_swing_window=BOS_SWING_WINDOW, retracement_window=RETRACEMENT_WINDOW):
    """Liquidity Sweep + FVG + BOS ketma-ketligidan keyin, market narxda DARHOL
    kirmasdan — narx BOS hosil qilgan Order Block (OB) yoki Fair Value Gap (FVG)
    zonasiga QAYTIB kelishini kutadi. Bu ikkalasidan biriga (OB YOKI FVG) qaytish
    signal beradi, chunki bu SL'ni ancha torroq va R:R'ni yaxshiroq qiladi.

    Agar narx zonaga qaytmasdan ketaversa — signal chiqmaydi (imkoniyat qo'ldan
    ketadi, lekin bu — sifat evaziga miqdordan voz kechish).
    Agar narx OB'ning narigi chetidan butunlay chiqib ketsa — signal bekor
    qilinadi (invalidate), chunki bu smart money niyati o'zgarganini bildiradi."""
    if len(df) < lookback:
        return None

    sub = df.iloc[-lookback:]
    highs = sub["high"].values
    lows = sub["low"].values
    closes = sub["close"].values
    opens = sub["open"].values
    times = sub.index
    n = len(sub)
    cur = n - 1

    avg_candle_range = (sub["high"] - sub["low"]).mean()
    min_fvg_size = avg_candle_range * min_fvg_mult
    min_sweep_depth = avg_candle_range * min_sweep_mult

    swing_high_idx, swing_low_idx = find_swing_points(highs, lows, window=bos_swing_window, exclude_last=True)
    nearest_swing_high_before = lambda idx: next((i for i in reversed(swing_high_idx) if i < idx), None)
    nearest_swing_low_before = lambda idx: next((i for i in reversed(swing_low_idx) if i < idx), None)

    def prominent_high(idx):
        start = max(0, idx - prominence_window)
        if idx - start < prominence_min_history:
            return None
        candidates = [i for i in swing_high_idx if start <= i < idx]
        return max(highs[i] for i in candidates) if candidates else None

    def prominent_low(idx):
        start = max(0, idx - prominence_window)
        if idx - start < prominence_min_history:
            return None
        candidates = [i for i in swing_low_idx if start <= i < idx]
        return min(lows[i] for i in candidates) if candidates else None

    search_start = max(bos_swing_window, cur - retracement_window)

    # --- BULLISH ---
    bos_m = None
    for m in range(search_start, cur):
        ref_idx = nearest_swing_high_before(m)
        if ref_idx is None:
            continue
        ref = highs[ref_idx]
        if closes[m] > ref and closes[m - 1] <= ref:
            bos_m = m  # eng so'nggisini olamiz

    if bos_m is not None:
        fvg_idx = None
        for j in range(max(2, bos_m - OB_SEARCH_BACK), bos_m + 1):
            if (lows[j] - highs[j - 2]) >= min_fvg_size:
                fvg_idx = j

        sweep_ok = False
        if fvg_idx is not None:
            for k in range(max(bos_swing_window, fvg_idx - prominence_window), fvg_idx):
                sl = prominent_low(k)
                if sl is not None and (sl - lows[k]) >= min_sweep_depth and closes[k] > sl:
                    sweep_ok = True
                    break

        if fvg_idx is not None and sweep_ok:
            fvg_top = lows[fvg_idx]
            fvg_bottom = highs[fvg_idx - 2]
            ob_idx = find_order_block(closes, opens, fvg_idx - 2, "bullish")
            if ob_idx is not None:
                zone_top = max(fvg_top, highs[ob_idx])
                zone_bottom = min(fvg_bottom, lows[ob_idx])

                first_touch = None
                invalidated = False
                for k in range(bos_m + 1, cur + 1):
                    if closes[k] < zone_bottom:
                        invalidated = True
                        break
                    if lows[k] <= zone_top and highs[k] >= zone_bottom:
                        first_touch = k
                        break

                if not invalidated and first_touch == cur and closes[cur] > zone_bottom:
                    return {
                        "type": "ob_fvg_bullish",
                        "bos_time": str(times[bos_m]),
                        "bos_level": highs[nearest_swing_high_before(bos_m)],
                        "fvg_time": str(times[fvg_idx]),
                        "ob_time": str(times[ob_idx]),
                        "zone_top": zone_top,
                        "zone_bottom": zone_bottom,
                        "entry_close": closes[cur],
                    }

    # --- BEARISH ---
    bos_m2 = None
    for m in range(search_start, cur):
        ref_idx = nearest_swing_low_before(m)
        if ref_idx is None:
            continue
        ref = lows[ref_idx]
        if closes[m] < ref and closes[m - 1] >= ref:
            bos_m2 = m

    if bos_m2 is not None:
        fvg_idx = None
        for j in range(max(2, bos_m2 - OB_SEARCH_BACK), bos_m2 + 1):
            if (lows[j - 2] - highs[j]) >= min_fvg_size:
                fvg_idx = j

        sweep_ok = False
        if fvg_idx is not None:
            for k in range(max(bos_swing_window, fvg_idx - prominence_window), fvg_idx):
                sh = prominent_high(k)
                if sh is not None and (highs[k] - sh) >= min_sweep_depth and closes[k] < sh:
                    sweep_ok = True
                    break

        if fvg_idx is not None and sweep_ok:
            fvg_bottom = highs[fvg_idx]
            fvg_top = lows[fvg_idx - 2]
            ob_idx = find_order_block(closes, opens, fvg_idx - 2, "bearish")
            if ob_idx is not None:
                zone_top = max(fvg_top, highs[ob_idx])
                zone_bottom = min(fvg_bottom, lows[ob_idx])

                first_touch = None
                invalidated = False
                for k in range(bos_m2 + 1, cur + 1):
                    if closes[k] > zone_top:
                        invalidated = True
                        break
                    if highs[k] >= zone_bottom and lows[k] <= zone_top:
                        first_touch = k
                        break

                if not invalidated and first_touch == cur and closes[cur] < zone_top:
                    return {
                        "type": "ob_fvg_bearish",
                        "bos_time": str(times[bos_m2]),
                        "bos_level": lows[nearest_swing_low_before(bos_m2)],
                        "fvg_time": str(times[fvg_idx]),
                        "ob_time": str(times[ob_idx]),
                        "zone_top": zone_top,
                        "zone_bottom": zone_bottom,
                        "entry_close": closes[cur],
                    }

    return None
