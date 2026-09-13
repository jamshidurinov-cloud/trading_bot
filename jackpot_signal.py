"""
jackpot_signal.py

Ikkita signal turi:
1. detect_ob_fvg_entry - Sweep + FVG + BOS asosidagi OB/FVG kirish strategiyasi
2. detect_jackpot_signal - YANGI (2026-09-12): Range (luxalgo_range_detector) +
   Spring/Upthrust (wyckoff_spring_upthrust) + FVG (luxalgo_smc) birlashtirilgan
   "JACKPOT" signali.

ESLATMA (2026-09-12): eski `detect_dynamic_range`, `detect_dynamic_spring_upthrust`
va `detect_jackpot_signal` (teng cho'qqi/tub klasterlash asosida) ~1000 ta
yig'ilgan signal ichida <1% chastota bilan ishlagani (amalda ishlamagani)
sababli OLIB TASHLANDI va yuqoridagi yangi arxitektura bilan ALMASHTIRILDI.

Yangi `detect_jackpot_signal` HAM eski bilan bir xil `"jackpot_spring"`/
`"jackpot_upthrust"` nomi va `event_low`/`event_high`/`range_high`/`range_low`
maydonlari bilan qaytariladi - shunda main.py'dagi SL/TP/yo'nalish/dedup
mantig'i (`compute_sl_level` va h.k.) HECH QANDAY o'zgarishisiz ishlaydi.

main.py bu fayldan quyidagilarni import qiladi:
    from jackpot_signal import find_swing_points, detect_ob_fvg_entry, detect_jackpot_signal
"""

import numpy as np

from luxalgo_range_detector import detect_luxalgo_range
from wyckoff_spring_upthrust import detect_wyckoff_springs, detect_wyckoff_upthrusts
from luxalgo_smc import detect_fvg

# ============================================================================
# SOZLAMALAR
# ============================================================================

PROMINENCE_WINDOW = 40      # Sweep uchun: "ajralib turgan" darajani aniqlash oynasi
PROMINENCE_MIN_HISTORY = 10  # ishonchli referens uchun kamida shuncha oldingi sveцha kerak
BOS_SWING_WINDOW = 6        # BOS uchun: eng yaqin tasdiqlangan swing nuqta oynasi
EVENT_SEARCH_WINDOW = 3     # "voqea" (event/test) uchun oxirgi nechta sveцhani tekshirish
                            # (bitta o'tkazib yuborilgan Cron ishga tushishiga chidamli
                            # bo'lish uchun)


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


# ============================================================================
# 🎰 JACKPOT (YANGI, 2026-09-12) — Range + Spring/Upthrust + FVG
# ============================================================================
#
# Zanjir:
#   1. luxalgo_range_detector.detect_luxalgo_range -> range (box_top/box_bottom)
#   2. wyckoff_spring_upthrust.detect_wyckoff_springs/upthrusts, LEKIN
#      external_level_series orqali - o'zining pivot qidiruvi EMAS, balki
#      bizning range chegaramiz (1-qadamdan) "daraja" sifatida ishlatiladi
#   3. Spring/Upthrust tasdiqlangach, luxalgo_smc.detect_fvg orqali, TO'G'RI
#      yo'nalishdagi (spring->bullish, upthrust->bearish), "yangi" (ko'p
#      uzoqlashmagan) FVG qidiriladi - xuddi luxalgo_signal.py'dagi kabi
#
# MUHIM: natija ESKI nom/maydonlar bilan qaytariladi ("jackpot_spring"/
# "jackpot_upthrust", event_low/event_high, range_high/range_low) - shunda
# main.py'dagi SL/TP/yo'nalish/dedup mantig'i o'zgarishisiz ishlaydi.
# Qo'shimcha: fvg_top/fvg_bottom (grafikda belgilash uchun, va kelajakda
# TP hisoblashda ishlatilishi mumkin).

JACKPOT_MAX_SPRING_ATTEMPTS = 3   # Spring/Upthrust uchun max muvaffaqiyatsiz urinish
JACKPOT_FRESH_FVG_WINDOW = 5      # FVG spring/upthrust'dan keyin "qancha uzoqlashishi" mumkin


def detect_jackpot_signal(df, lookback=300, range_length=20, range_mult=1.0, range_atr_len=500,
                            max_spring_attempts=JACKPOT_MAX_SPRING_ATTEMPTS,
                            fresh_fvg_window=JACKPOT_FRESH_FVG_WINDOW):
    """YANGI JACKPOT: Range + Spring/Upthrust + FVG.

    Qaytaradi (topilsa): eski JACKPOT bilan bir xil asosiy maydonlar
    (type, range_high, range_low, event_time, event_low/event_high,
    current_close) + YANGI: fvg_time, fvg_top, fvg_bottom."""
    if len(df) < lookback:
        return None

    sub = df.iloc[-lookback:].copy()
    n = len(sub)
    cur = n - 1
    closes = sub["close"].to_numpy(dtype=float)
    times = sub.index

    range_states = detect_luxalgo_range(sub, length=range_length, mult=range_mult, atr_len=range_atr_len)
    box_bottom_series = [r["box_bottom"] if r["box_bottom"] is not None else float("nan") for r in range_states]
    box_top_series = [r["box_top"] if r["box_top"] is not None else float("nan") for r in range_states]

    fvgs = detect_fvg(sub, auto_threshold=True)

    # --- SPRING (bullish) ---
    springs = detect_wyckoff_springs(sub, external_level_series=box_bottom_series,
                                       max_attempts=max_spring_attempts)
    spring_reason = "range/spring topilmadi"
    if springs:
        last_spring = springs[-1]
        spring_reason = f"spring @{last_spring['confirm_idx']} (lvl={last_spring['pivot_level']:.2f}) topildi, lekin mos FVG yo'q"
        matching_fvgs = [f for f in fvgs if f["direction"] == "bullish"
                          and f["confirm_idx"] > last_spring["confirm_idx"]]
        if matching_fvgs:
            fvg = max(matching_fvgs, key=lambda f: f["confirm_idx"])
            if fvg["confirm_idx"] >= cur - fresh_fvg_window + 1:
                event_idx = last_spring["confirm_idx"]
                range_high_val = box_top_series[event_idx] if not np.isnan(box_top_series[event_idx]) else None
                range_high_str = f"{range_high_val:.2f}" if range_high_val is not None else "?"
                print(f"[JACKPOT SIGNAL] SPRING: range=({last_spring['pivot_level']:.2f}-{range_high_str}) "
                      f"spring@{event_idx}({sub['low'].iloc[event_idx]:.2f}) "
                      f"fvg@{fvg['confirm_idx']}({fvg['bottom']:.2f}-{fvg['top']:.2f}) "
                      f"narx={closes[cur]:.2f}")
                return {
                    "type": "jackpot_spring",
                    "range_high": range_high_val,
                    "range_low": last_spring["pivot_level"],
                    "event_time": str(times[event_idx]),
                    "event_low": sub["low"].iloc[event_idx],
                    "current_close": closes[cur],
                    "fvg_time": str(times[fvg["confirm_idx"]]),
                    "fvg_top": fvg["top"],
                    "fvg_bottom": fvg["bottom"],
                }
            spring_reason = f"spring @{last_spring['confirm_idx']} + FVG topildi, lekin eskirgan"

    # --- UPTHRUST (bearish) ---
    upthrusts = detect_wyckoff_upthrusts(sub, external_level_series=box_top_series,
                                           max_attempts=max_spring_attempts)
    upthrust_reason = "range/upthrust topilmadi"
    if upthrusts:
        last_upthrust = upthrusts[-1]
        upthrust_reason = f"upthrust @{last_upthrust['confirm_idx']} (lvl={last_upthrust['pivot_level']:.2f}) topildi, lekin mos FVG yo'q"
        matching_fvgs = [f for f in fvgs if f["direction"] == "bearish"
                          and f["confirm_idx"] > last_upthrust["confirm_idx"]]
        if matching_fvgs:
            fvg = max(matching_fvgs, key=lambda f: f["confirm_idx"])
            if fvg["confirm_idx"] >= cur - fresh_fvg_window + 1:
                event_idx = last_upthrust["confirm_idx"]
                range_low_val = box_bottom_series[event_idx] if not np.isnan(box_bottom_series[event_idx]) else None
                range_low_str = f"{range_low_val:.2f}" if range_low_val is not None else "?"
                print(f"[JACKPOT SIGNAL] UPTHRUST: range=({range_low_str}-{last_upthrust['pivot_level']:.2f}) "
                      f"upthrust@{event_idx}({sub['high'].iloc[event_idx]:.2f}) "
                      f"fvg@{fvg['confirm_idx']}({fvg['bottom']:.2f}-{fvg['top']:.2f}) "
                      f"narx={closes[cur]:.2f}")
                return {
                    "type": "jackpot_upthrust",
                    "range_high": last_upthrust["pivot_level"],
                    "range_low": range_low_val,
                    "event_time": str(times[event_idx]),
                    "event_high": sub["high"].iloc[event_idx],
                    "current_close": closes[cur],
                    "fvg_time": str(times[fvg["confirm_idx"]]),
                    "fvg_top": fvg["top"],
                    "fvg_bottom": fvg["bottom"],
                }
            upthrust_reason = f"upthrust @{last_upthrust['confirm_idx']} + FVG topildi, lekin eskirgan"

    print(f"[JACKPOT DEBUG] bullish: {spring_reason} || bearish: {upthrust_reason}")
    return None
