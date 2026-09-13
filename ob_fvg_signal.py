"""
ob_fvg_signal.py

Sweep + FVG + BOS asosidagi OB/FVG kirish strategiyasi (detect_ob_fvg_entry).

ESLATMA (2026-09-12): bu funksiya avval `jackpot_signal.py` faylining ichida
edi, YANGI `detect_jackpot_signal` bilan bir joyda turgan edi. Ikkalasi
bir-biriga aloqasi yo'q, MUSTAQIL strategiyalar bo'lgani uchun, kelajakda
har birini alohida, bir-biriga tegmasdan o'zgartirish/sinash imkoniyati
uchun BO'LIB TASHLANDI (Jamshid so'roviga ko'ra).

main.py bu fayldan quyidagilarni import qiladi:
    from ob_fvg_signal import find_swing_points, detect_ob_fvg_entry
"""

# ============================================================================
# SOZLAMALAR
# ============================================================================

PROMINENCE_WINDOW = 40      # Sweep uchun: "ajralib turgan" darajani aniqlash oynasi
PROMINENCE_MIN_HISTORY = 10  # ishonchli referens uchun kamida shuncha oldingi sveцha kerak
BOS_SWING_WINDOW = 6        # BOS uchun: eng yaqin tasdiqlangan swing nuqta oynasi
EVENT_SEARCH_WINDOW = 3     # "voqea" (event/test) uchun oxirgi nechta sveцhani tekshirish
                            # (bitta o'tkazib yuborilgan Cron ishga tushishiga chidamli
                            # bo'lish uchun)
RETRACEMENT_WINDOW = 30   # BOS'dan keyin retracement uchun necha sveцha kutish
OB_SEARCH_BACK = 10       # Order Block'ni FVG'dan necha sveцha orqaga qarab qidirish


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
# OB/FVG RETRACEMENT ENTRY — BOS'dan keyin, OB yoki FVG zonasiga qaytishni kutadi
# ============================================================================

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
                          bos_swing_window=BOS_SWING_WINDOW, retracement_window=RETRACEMENT_WINDOW,
                          event_search_window=EVENT_SEARCH_WINDOW):
    """Liquidity Sweep + FVG + BOS ketma-ketligidan keyin, market narxda DARHOL
    kirmasdan — narx BOS hosil qilgan Order Block (OB) yoki Fair Value Gap (FVG)
    zonasiga QAYTIB kelishini kutadi. Bu ikkalasidan biriga (OB YOKI FVG) qaytish
    signal beradi, chunki bu SL'ni ancha torroq va R:R'ni yaxshiroq qiladi.

    Zonaga birinchi tegish aynan JORIY svechada emas, balki so'nggi
    `event_search_window` sveцha ichida bo'lgan bo'lishi mumkin (bitta
    o'tkazib yuborilgan Cron ishga tushishiga chidamli bo'lish uchun) — faqat
    shu oraliqda zona keyinchalik buzilmagan bo'lishi shart.

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
        """idx'dan oldingi oynadagi HAQIQIY, hali BUZILMAGAN eng yuqori swing
        cho'qqisi (eng ekstremalidan boshlab tekshiriladi)."""
        start = max(0, idx - prominence_window)
        if idx - start < prominence_min_history:
            return None
        candidates = [i for i in swing_high_idx if start <= i < idx]
        if not candidates:
            return None
        for i in sorted(candidates, key=lambda i: -highs[i]):
            level = highs[i]
            segment_max = highs[i + 1:idx].max() if i + 1 < idx else -float("inf")
            if segment_max <= level:
                return level
        return None

    def prominent_low(idx):
        """idx'dan oldingi oynadagi HAQIQIY, hali BUZILMAGAN eng past swing
        tubi (eng ekstremalidan boshlab tekshiriladi)."""
        start = max(0, idx - prominence_window)
        if idx - start < prominence_min_history:
            return None
        candidates = [i for i in swing_low_idx if start <= i < idx]
        if not candidates:
            return None
        for i in sorted(candidates, key=lambda i: lows[i]):
            level = lows[i]
            segment_min = lows[i + 1:idx].min() if i + 1 < idx else float("inf")
            if segment_min >= level:
                return level
        return None

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
                    if first_touch is None and lows[k] <= zone_top and highs[k] >= zone_bottom:
                        first_touch = k  # topilgach ham davom etamiz - keyingi invalidatsiyani ham tekshirish uchun

                is_recent = first_touch is not None and (cur - first_touch) < event_search_window
                if not invalidated and is_recent and closes[cur] > zone_bottom:
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
                    if first_touch is None and highs[k] >= zone_bottom and lows[k] <= zone_top:
                        first_touch = k

                is_recent = first_touch is not None and (cur - first_touch) < event_search_window
                if not invalidated and is_recent and closes[cur] < zone_top:
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
