"""
ob_fvg_signal.py

BOS/CHoCH + Order Block + FVG asosidagi, RETEST kutuvchi kirish
strategiyasi (detect_ob_fvg_entry).

ESLATMA (2026-09-13): bu fayl AVVAL o'zining qo'lda yozilgan, oddiy
formulalariga (find_swing_points, find_order_block, qo'lda FVG hisoblash)
asoslangan edi. Jamshid tekshiruvi natijasida bu - juda sodda, ko'p xato
qiladigan (masalan Order Block'ning mitigatsiyasi UMUMAN kuzatilmagan)
ekanligi aniqlandi. SHU SABABLI, mantiq TO'LIQ QAYTA QURILDI - endi
LuxAlgo'ning tayyor, aniq portlangan va sinalgan kutubxonalariga tayanadi:

    BOS/CHoCH     -> luxalgo_smc.detect_bos_choch
    Order Block   -> luxalgo_smc.detect_order_blocks (mitigatsiya BILAN!)
    FVG           -> luxalgo_smc.detect_fvg

ESLATMA 2 (2026-09-13): dastlab bu yerda "sweep" ham majburiy shart edi.
Jamshid bilan muhokama va tekshiruv natijasida (LuxAlgo'ning o'zining
detect_bos_choch funksiyasi sweep'ga UMUMAN bog'liq emasligi, va SMC
nazariyasida bu - faqat ICT'ning qattiqroq "MSS" versiyasida majburiy,
ODDIY (generic) SMC BOS/CHoCH'da esa - MAJBURIY EMAS, faqat qo'shimcha
ishonch omili ekanligi aniqlandi) - sweep talabi OLIB TASHLANDI.

ESLATMA 3 (2026-09-13): FVG va OB endi BIRLASHTIRILMAYDI (avval
zone_top/bottom = ikkalasining eng kengroq birikmasi edi). Endi - ANIQ
USTUVORLIK ZANJIRI: FVG mavjud VA hali "bosib o'tilmagan" (mitigatsiya
bo'lmagan) bo'lsa - FAQAT FVG zonasi ishlatiladi (torroq, aniqroq).
FVG yo'q BO'LSA, YOKI FVG allaqachon bosib o'tilgan (mitigatsiya bo'lgan)
bo'lsa - OB zonasiga (agar u hali mitigatsiya bo'lmagan bo'lsa) qaytiladi.

Strategiyaning O'ZIGA XOS qismi (LuxAlgo'da yo'q, shu faylning o'zida
qoladi) - "RETEST kutish": BOS/CHoCH'dan keyin, narx zonaga QAYTIB
kelishini kutadi, DARHOL kirmaydi.

Fayl nomi eskisi bilan bir xil qoldirildi (Jamshid so'roviga ko'ra),
funksiya nomi ham o'zgarmadi - main.py'ga hech qanday qo'shimcha
o'zgarish kerak emas:
    from ob_fvg_signal import find_swing_points, detect_ob_fvg_entry
"""

from luxalgo_smc import detect_bos_choch, detect_order_blocks, detect_fvg

# ============================================================================
# SOZLAMALAR
# ============================================================================

BOS_SIZE = 6                # BOS/CHoCH uchun pivot aniqlash oynasi
FVG_SEARCH_BACK = 10        # Order Block manbaidan necha bar orqaga qarab FVG qidirish
EVENT_SEARCH_WINDOW = 3     # Retest "yangi" hisoblanishi uchun oxirgi nechta bar
                            # (bitta o'tkazib yuborilgan Cron ishga tushishiga chidamli
                            # bo'lish uchun)


# ============================================================================
# ESKI YORDAMCHI FUNKSIYA - SAQLAB QOLINDI (boshqa joyda ishlatilishi mumkin,
# masalan main.py'ning eski detect_smc_composite funksiyasi buni chaqiradi)
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
# OB/FVG RETRACEMENT ENTRY — BOS'dan keyin, zonaga qaytishni kutadi
# ============================================================================

def _check_retest(direction, zone_top, zone_bottom, pattern_end_idx, closes, highs, lows,
                   cur, event_search_window):
    """Berilgan zona uchun retest bo'lganini tekshiradi. Qaytaradi:
    (True/False, sabab_matni)."""
    first_touch = None
    invalidated = False
    for k in range(pattern_end_idx + 1, cur + 1):
        if direction == "bullish" and closes[k] < zone_bottom:
            invalidated = True
            break
        if direction == "bearish" and closes[k] > zone_top:
            invalidated = True
            break
        if first_touch is None and lows[k] <= zone_top and highs[k] >= zone_bottom:
            first_touch = k  # topilgach ham davom etamiz - keyingi invalidatsiyani tekshirish uchun

    if invalidated:
        return False, "INVALIDATSIYA bo'ldi"

    is_recent = first_touch is not None and (cur - first_touch) < event_search_window
    if not is_recent:
        return False, f"retest {'yoq' if first_touch is None else 'eskirgan'}"

    if direction == "bullish" and not (closes[cur] > zone_bottom):
        return False, "retest bor, lekin joriy narx zonadan pastda"
    if direction == "bearish" and not (closes[cur] < zone_top):
        return False, "retest bor, lekin joriy narx zonadan yuqorida"

    return True, None


def _try_direction(direction, bos_events, order_blocks, fvgs, sub, cur,
                    fvg_search_back, event_search_window):
    """Bitta yo'nalish (bullish/bearish) uchun to'liq zanjirni sinab ko'radi:
    BOS/CHoCH -> shu voqeaga tegishli Order Block bormi (hali mitigatsiya
    bo'lmagan) -> OB atrofida FVG bormi:
      - FVG bor VA hali mitigatsiya bo'lmagan -> FAQAT FVG zonasi bilan sinaladi
      - FVG yo'q, YOKI FVG allaqachon mitigatsiya bo'lgan -> OB zonasi bilan sinaladi
    -> RETEST bo'lganmi (yangi va invalidatsiya qilinmagan).

    Eng so'nggi (eng yaqin) BOS/CHoCH'dan boshlab, eskisiga qarab qidiradi -
    birinchi to'liq mos kelgan zanjir qaytariladi."""
    closes = sub["close"].to_numpy(dtype=float)
    highs = sub["high"].to_numpy(dtype=float)
    lows = sub["low"].to_numpy(dtype=float)
    times = sub.index

    dir_events = [e for e in bos_events if e["direction"] == direction]
    if not dir_events:
        return None, f"{direction}: BOS/CHoCH yo'q"

    last_reason = f"{direction}: mos zanjir topilmadi"
    reason_captured = False

    for event in sorted(dir_events, key=lambda e: e["break_idx"], reverse=True):
        ob = next((o for o in order_blocks
                   if o["created_idx"] == event["break_idx"] and o["direction"] == direction), None)
        if ob is None:
            if not reason_captured:
                last_reason = f"{direction}: BOS @{event['break_idx']} topildi, lekin Order Block yo'q"
                reason_captured = True
            continue

        ob_usable = ob["mitigated_idx"] is None or ob["mitigated_idx"] > cur

        fvg_search_start = max(0, ob["source_idx"] - fvg_search_back)
        fvg_search_end = event["break_idx"] + fvg_search_back
        candidate_fvgs = [
            f for f in fvgs
            if f["direction"] == direction and fvg_search_start <= f["confirm_idx"] <= fvg_search_end
        ]
        fvg = max(candidate_fvgs, key=lambda f: f["confirm_idx"]) if candidate_fvgs else None
        fvg_usable = fvg is not None and (fvg["mitigated_idx"] is None or fvg["mitigated_idx"] > cur)

        if fvg_usable:
            # 1-USTUVORLIK: FVG mavjud va hali "bosib o'tilmagan" - FAQAT shu zona bilan sinaladi
            zone_top, zone_bottom = fvg["top"], fvg["bottom"]
            pattern_end_idx = max(event["break_idx"], fvg["confirm_idx"])
            ok, fail_reason = _check_retest(direction, zone_top, zone_bottom, pattern_end_idx,
                                              closes, highs, lows, cur, event_search_window)
            if ok:
                return {
                    "type": f"ob_fvg_{direction}",
                    "bos_time": str(times[event["break_idx"]]),
                    "bos_level": event["level"],
                    "fvg_time": str(times[fvg["confirm_idx"]]),
                    "ob_time": str(times[ob["source_idx"]]),
                    "zone_top": zone_top,
                    "zone_bottom": zone_bottom,
                    "fvg_top": fvg["top"],
                    "fvg_bottom": fvg["bottom"],
                    "ob_top": ob["bar_high"],
                    "ob_bottom": ob["bar_low"],
                    "entry_close": closes[cur],
                }, None
            if not reason_captured:
                last_reason = f"{direction}: FVG zonasi @{event['break_idx']} topildi, lekin {fail_reason}"
                reason_captured = True
            continue

        # 2-USTUVORLIK: FVG yo'q yoki bosib o'tilgan - OB zonasiga qaytiladi
        if not ob_usable:
            if not reason_captured:
                last_reason = (f"{direction}: OB @{event['break_idx']} topildi, lekin FVG yo'q/bosib o'tilgan "
                                f"VA OB ham allaqachon mitigatsiya bo'lgan")
                reason_captured = True
            continue

        zone_top, zone_bottom = ob["bar_high"], ob["bar_low"]
        ok, fail_reason = _check_retest(direction, zone_top, zone_bottom, event["break_idx"],
                                          closes, highs, lows, cur, event_search_window)
        if ok:
            return {
                "type": f"ob_fvg_{direction}",
                "bos_time": str(times[event["break_idx"]]),
                "bos_level": event["level"],
                "fvg_time": None,
                "ob_time": str(times[ob["source_idx"]]),
                "zone_top": zone_top,
                "zone_bottom": zone_bottom,
                "ob_top": ob["bar_high"],
                "ob_bottom": ob["bar_low"],
                "entry_close": closes[cur],
            }, None
        if not reason_captured:
            last_reason = f"{direction}: OB zonasi @{event['break_idx']} topildi (FVG yo'q), lekin {fail_reason}"
            reason_captured = True

    return None, last_reason


def detect_ob_fvg_entry(df, lookback=300, bos_size=BOS_SIZE,
                          fvg_search_back=FVG_SEARCH_BACK, event_search_window=EVENT_SEARCH_WINDOW):
    """BOS/CHoCH + Order Block + FVG ketma-ketligidan keyin, market narxda
    DARHOL kirmasdan — narx zonaga QAYTIB kelishini kutadi.

    Zona ustuvorligi: FVG mavjud va hali bosib o'tilmagan bo'lsa - FAQAT FVG
    (torroq). Aks holda - OB (agar u hali mitigatsiya bo'lmagan bo'lsa).

    Agar narx zonaga qaytmasdan ketaversa — signal chiqmaydi.
    Agar narx zonadan (bullish uchun pastga, bearish uchun yuqoriga)
    butunlay chiqib ketsa — signal bekor qilinadi (invalidate)."""
    # MUHIM (2026-09-14): qattiq "len(df) < lookback -> None" o'chirildi -
    # bitta-ikkita sham kam kelsa ham, MAVJUD qancha sham bo'lsa - o'shani
    # ishlatadi. Faqat mutlaqo yetarsiz (bos_size uchun ham yetmaydigan)
    # holatda to'xtaydi.
    if len(df) < bos_size * 2 + 5:
        return None

    sub = df.iloc[-min(lookback, len(df)):].copy()
    n = len(sub)
    cur = n - 1

    bos_events = detect_bos_choch(sub, size=bos_size)
    order_blocks = detect_order_blocks(sub, bos_events)
    fvgs = detect_fvg(sub, auto_threshold=True)

    bullish_signal, bull_reason = _try_direction(
        "bullish", bos_events, order_blocks, fvgs, sub, cur, fvg_search_back, event_search_window,
    )
    if bullish_signal:
        print(f"[OB_FVG SIGNAL] BULLISH: bos={bullish_signal['bos_level']:.2f} "
              f"zona=({bullish_signal['zone_bottom']:.2f}-{bullish_signal['zone_top']:.2f}) "
              f"manba={'FVG' if bullish_signal['fvg_time'] else 'OB'} "
              f"narx={bullish_signal['entry_close']:.2f}")
        return bullish_signal

    bearish_signal, bear_reason = _try_direction(
        "bearish", bos_events, order_blocks, fvgs, sub, cur, fvg_search_back, event_search_window,
    )
    if bearish_signal:
        print(f"[OB_FVG SIGNAL] BEARISH: bos={bearish_signal['bos_level']:.2f} "
              f"zona=({bearish_signal['zone_bottom']:.2f}-{bearish_signal['zone_top']:.2f}) "
              f"manba={'FVG' if bearish_signal['fvg_time'] else 'OB'} "
              f"narx={bearish_signal['entry_close']:.2f}")
        return bearish_signal

    print(f"[OB_FVG DEBUG] {bull_reason} || {bear_reason}")
    return None
