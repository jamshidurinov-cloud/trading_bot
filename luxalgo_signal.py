"""
luxalgo_signal.py

LuxAlgo'ning ANIQ portlangan kutubxonalari (luxalgo_smc.py - FVG/BOS-CHoCH,
luxalgo_liquidity_sweeps.py - Sweep) asosida signal aniqlaydi. Eski
smc_lib.py + sweep_lib.py o'rnini bosadi.

Mantiq (avvalgi smc_official signalimiz bilan bir xil tuzilma, lekin
LuxAlgo'ning aniq FVG/Sweep algoritmlari bilan):

1. Sweep (wick_sweep yoki outbreak_retest, LuxAlgo Liquidity Sweeps'dan)
2. Undan KEYIN hosil bo'lgan, mos yo'nalishdagi FVG (LuxAlgo SMC'dan,
   Auto Threshold + yopilish sharti bilan)
3. Bir nechta sweep bir xil FVG'ga mos kelsa - ENG SO'NGGI (yangi) sweep
   tanlanadi (eski sweep_lib'dagi tuzatishimiz bilan bir xil tamoyil)
4. BOS/CHoCH (LuxAlgo SMC'dan) - QO'SHIMCHA, majburiy emas
"""

from luxalgo_smc import detect_fvg, detect_bos_choch
from luxalgo_liquidity_sweeps import detect_luxalgo_liquidity_sweeps


def detect_luxalgo_signal(df, lookback=144, swing_length=6, fresh_break_window=5,
                            sweep_mode="wicks_and_outbreak_retest", sl_buffer=0.20):
    """LuxAlgo Sweep+FVG signalini aniqlaydi.

    Qaytaradi: eski smc_official signal bilan bir xil tuzilmadagi lug'at
    (type, sweep_level, sweep_time, fvg_time, fvg_top, fvg_bottom,
    has_structure, structure_kind, current_close) - main.py'dagi mavjud
    compute_sl_level/log_new_signal kodiga mos kelishi uchun."""
    sub = df.iloc[-lookback:].copy()
    if len(sub) < lookback:
        return None

    n = len(sub)
    cur = n - 1
    times = sub.index

    sweeps = detect_luxalgo_liquidity_sweeps(sub, length=swing_length, mode=sweep_mode)
    fvgs = detect_fvg(sub, auto_threshold=True)
    bos_events = detect_bos_choch(sub, size=swing_length)

    def find_signal(direction):
        candidates = [s for s in sweeps if s["direction"] == direction]
        if not candidates:
            return None, "hech qanday sweep topilmadi"

        fvg_dir = direction  # FVG yo'nalishi sweep yo'nalishi bilan BIR XIL
        # (chunki bullish sweep = pastdagi trap/retest, undan keyin BULLISH FVG kutiladi)
        best = None
        rejected = []
        # ENG YANGI sweep'dan boshlab tekshiramiz (eski sweep muammosidan qochish uchun)
        for sw in reversed(candidates):
            sweep_idx = sw["signal_idx"]
            matching_fvgs = [f for f in fvgs if f["direction"] == fvg_dir
                              and f["confirm_idx"] > sweep_idx
                              and f["mitigated_idx"] is None]
            if not matching_fvgs:
                rejected.append(f"sweep@{sweep_idx}(lvl={sw['pivot_level']:.2f},{sw['kind']}) - keyin mos FVG yo'q")
                continue
            # SODDALASHTIRILDI: "eng yaxshisi"ni qidirmasdan, TOPILGAN BIRINCHI
            # mos FVG bilan darhol signal beriladi (murakkab tanlov olib tashlandi)
            fvg = matching_fvgs[0]
            fvg_idx = fvg["confirm_idx"]

            if fvg_idx < cur - fresh_break_window + 1:
                rejected.append(f"sweep@{sweep_idx} + FVG@{fvg_idx} - ESKIRGAN (cur={cur})")
                continue

            best = {
                "sweep_idx": sweep_idx,
                "sweep_level": sw["pivot_level"],
                "sweep_kind": sw["kind"],
                "fvg_idx": fvg_idx,
                "fvg_top": fvg["top"],
                "fvg_bottom": fvg["bottom"],
            }
            break  # birinchi mos juftlik topildi - qidiruvni tox tatamiz

        if best is None:
            last_sweep = candidates[-1]
            reason = (f"{len(candidates)} ta sweep topildi (eng so'nggisi: @{last_sweep['signal_idx']}, "
                      f"lvl={last_sweep['pivot_level']:.2f}, {last_sweep['kind']}), lekin hech biri mos FVG "
                      f"bilan bog'lanmadi. " + " | ".join(rejected[-3:]))
            return None, reason

        has_structure = False
        structure_kind = None
        for ev in bos_events:
            if ev["direction"] == direction and best["sweep_idx"] <= ev["break_idx"] <= cur:
                has_structure = True
                structure_kind = ev["kind"]

        return {
            "type": f"luxalgo_{direction}",
            "sweep_time": str(times[best["sweep_idx"]]),
            "sweep_level": best["sweep_level"],
            "fvg_time": str(times[best["fvg_idx"]]),
            "fvg_top": best["fvg_top"],
            "fvg_bottom": best["fvg_bottom"],
            "has_structure": has_structure,
            "structure_kind": structure_kind,
            "current_close": float(sub["close"].iloc[cur]),
        }, None

    bullish_signal, bull_reason = find_signal("bullish")
    bearish_signal, bear_reason = find_signal("bearish")

    if bullish_signal is None and bearish_signal is None:
        print(f"[LUXALGO DEBUG] bullish: {bull_reason} || bearish: {bear_reason}")

    if bullish_signal and bearish_signal:
        return bullish_signal if bullish_signal["fvg_time"] >= bearish_signal["fvg_time"] else bearish_signal
    return bullish_signal or bearish_signal
