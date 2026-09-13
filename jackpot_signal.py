"""
jackpot_signal.py

YANGI JACKPOT (2026-09-12): Range (luxalgo_range_detector) + Spring/Upthrust
(wyckoff_spring_upthrust) + FVG (luxalgo_smc) birlashtirilgan "JACKPOT" signali.

ESLATMA: bu fayl avval `detect_ob_fvg_entry`ni ham o'z ichiga olardi -
ikkalasi bir-biriga aloqasi yo'q, MUSTAQIL strategiyalar bo'lgani uchun,
har birini alohida sinash/o'zgartirish imkoniyati uchun BO'LIB TASHLANDI.
OB/FVG endi `ob_fvg_signal.py`da.

ESLATMA (2026-09-12, yana): bu fayl avval yana uchta funksiyani ham o'z
ichiga olardi - `detect_dynamic_range` (teng cho'qqi/tub klasterlash
asosida), `detect_dynamic_spring_upthrust` va eski `detect_jackpot_signal`
(Spring/Upthrust + Test). Булар ~1000 ta yig'ilgan signal ichida <1%
chastota bilan ishlagani (amalda ishlamagani) sababli OLIB TASHLANDI va
quyidagi yangi arxitektura bilan ALMASHTIRILDI.

Yangi `detect_jackpot_signal` HAM eski bilan bir xil `"jackpot_spring"`/
`"jackpot_upthrust"` nomi va `event_low`/`event_high`/`range_high`/`range_low`
maydonlari bilan qaytariladi - shunda main.py'dagi SL/TP/yo'nalish/dedup
mantig'i (`compute_sl_level` va h.k.) HECH QANDAY o'zgarishisiz ishlaydi.

main.py bu fayldan quyidagilarni import qiladi:
    from jackpot_signal import detect_jackpot_signal
"""

import numpy as np

from luxalgo_range_detector import detect_luxalgo_range
from wyckoff_spring_upthrust import detect_wyckoff_springs, detect_wyckoff_upthrusts
from luxalgo_smc import detect_fvg
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
