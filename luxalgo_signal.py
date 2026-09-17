"""
luxalgo_signal.py

LuxAlgo'ning ANIQ portlangan kutubxonalari (luxalgo_smc.py - FVG/BOS-CHoCH,
luxalgo_liquidity_sweeps.py - Sweep) asosida signal aniqlaydi. Eski
smc_lib.py + sweep_lib.py o'rnini bosadi.

Mantiq (avvalgi smc_official signalimiz bilan bir xil tuzilma, lekin
LuxAlgo'ning aniq FVG/Sweep algoritmlari bilan):

1. Sweep (FAQAT wick_sweep - 2026-09-17'da outbreak_retest OLIB TASHLANDI,
   pastdagi izohga qarang)
2. FAQAT ENG SO'NGGI (xronologik eng yangi) sweep ko'rib chiqiladi - undan
   oldingi sweep'lar UMUMAN solishtirilmaydi (SMC nazariyasiga mos: sweep
   va undan keyingi FVG bitta uzluksiz institutsional harakatning qismi)
3. Undan KEYIN hosil bo'lgan, mos yo'nalishdagi, hali "yangi" (fresh_break_window
   ichida) FVG (LuxAlgo SMC'dan, Auto Threshold + yopilish sharti bilan)
4. BOS/CHoCH (LuxAlgo SMC'dan) - QO'SHIMCHA, majburiy emas

MUHIM (2026-09-17, Jamshid so'roviga ko'ra): avval sweep_mode
"wicks_and_outbreak_retest" edi - bu ikki TAMOMILA BOSHQA ma'noli hodisani
("wick_sweep" = klassik rad etish/reversal, "outbreak_retest" = buzilib
qayta sinalgan continuation) bitta ro'yxatda aralashtirib, faqat
xronologik "eng so'nggisi"ni tanlardi - kind'idan qat'iy nazar. Bu, masalan,
eski, aloqasiz bir outbreak_retest'ni ham "sweep" sifatida olib qo'yishi
mumkin edi. Endi sweep_mode="only_wicks" - faqat klassik wick_sweep
(SMC'dagi sof "liquidity grab" ta'rifiga mos) ishlatiladi,
outbreak_retest butunlay chiqarib tashlandi.
"""

from luxalgo_smc import detect_fvg, detect_bos_choch
from luxalgo_liquidity_sweeps import detect_luxalgo_liquidity_sweeps


def detect_luxalgo_signal(df, lookback=300, swing_length=6, fresh_break_window=5,
                            sweep_mode="only_wicks", sl_buffer=0.20):
    """LuxAlgo Sweep+FVG signalini aniqlaydi.

    Qaytaradi: eski smc_official signal bilan bir xil tuzilmadagi lug'at
    (type, sweep_level, sweep_time, fvg_time, fvg_top, fvg_bottom,
    has_structure, structure_kind, current_close) - main.py'dagi mavjud
    compute_sl_level/log_new_signal kodiga mos kelishi uchun.

    MUHIM (144 -> 300): avval `lookback=144` edi, bu TwelveData'dan olinadigan
    ma'lumot chegarasiga moslashtirib tanlangan son edi (texnik limit, strategik
    tanlov emas). Endi cTrader (Worker) orqali 300 tagacha sham ishonchli
    olinayotgani uchun, `lookback=300`ga oshirildi - bu ko'proq sweep/pivot
    darajasini "ko'rish imkoniyati"ni beradi (eski darajalar ko'zdan chetda
    qolmaydi). Bu XAVFSIZ, chunki eskirgan (uzoq muddatli) sweep+FVG
    kombinatsiyalari alohida, `fresh_break_window` orqali (`cur`ga nisbatan)
    rad etiladi - pastdagi find_signal() ichida, oyna kattaligidan qat'iy
    nazar. FVG threshold esa (`detect_fvg`da) oynaning boshidan kumulyativ
    o'rtacha asosida hisoblanadi - bu, oyna kattaligiga qarab, biroz farqli
    sezgirlik berishi MUMKIN (yaxshi yoki yomon tomonga - buni bozor
    ochilgach, 144 va 300 natijalarini solishtirib aniqlash tavsiya etiladi).

    MUHIM (2026-09-14): qattiq "len(sub) < lookback -> None" o'chirildi -
    bitta-ikkita sham kam kelsa ham (masalan 299 ta, 300 emas), BUTUNLAY
    to'xtab qolmasdan, MAVJUD qancha sham bo'lsa - o'shani ishlatadi."""
    sub = df.iloc[-min(lookback, len(df)):].copy()
    if len(sub) < swing_length * 2 + 5:
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

        # MUHIM TUZATISH (SMC nazariyasiga moslashtirildi, Jamshid tasdiqladi):
        # avval BARCHA topilgan sweep'lar bo'yicha aylanib, ular orasidan
        # "eng yangi FVG'li" kombinatsiya tanlanardi — bu esa, masalan,
        # 250 sham oldingi ESKI bir sweep'ni ham, agar unga keyinroq (hozirgi
        # vaqtga yaqin) biror FVG "tasodifan" mos kelib qolsa, signal deb
        # olib qo'yishi mumkin edi. Bu SMC nazariyasiga zid: real bozorda
        # sweep va undan keyingi FVG — BITTA uzluksiz institutsional
        # harakatning (sweep -> impuls -> FVG) qismlari, ular orasida
        # boshqa, aloqasi yo'q eski sweep'lar "aralashib" ketmasligi kerak.
        # Endi: FAQAT eng so'nggi (candidates[-1], xronologik eng yangi)
        # sweep ko'rib chiqiladi — undan oldingi sweep'lar UMUMAN
        # solishtirilmaydi. Aniq masofa (necha sham) chegarasi hozircha
        # ATAYLAB qo'yilmagan (Jamshid qarori) — faqat "eng so'nggi
        # sweep'dan keyin" degan tartib talab qilinadi.
        last_sweep = candidates[-1]
        sweep_idx = last_sweep["signal_idx"]

        matching_fvgs = [f for f in fvgs if f["direction"] == fvg_dir
                          and f["confirm_idx"] > sweep_idx
                          and f["mitigated_idx"] is None]
        if not matching_fvgs:
            reason = (f"eng so'nggi sweep @{sweep_idx} (lvl={last_sweep['pivot_level']:.2f}, "
                      f"{last_sweep['kind']}) topildi, lekin undan keyin mos FVG yo'q "
                      f"(jami {len(candidates)} ta sweep bor edi, faqat eng so'nggisi tekshirildi)")
            return None, reason

        fvg = max(matching_fvgs, key=lambda f: f["confirm_idx"])
        fvg_idx = fvg["confirm_idx"]

        # KRITIK TUZATISH (2026-09-15, Jamshid grafik orqali topdi - bugun
        # jackpot'da topib tuzatgan XUDDI SHU xato, bu yerda SMC'da ham bor
        # ekan): sweep bilan FVG orasida narx sweep zonasining QARAMA-QARSHI
        # tomonidan (bearish uchun sweep_area_top'dan YUQORIGA, bullish
        # uchun sweep_area_bottom'dan PASTGA) chiqib ketgan bo'lsa - bu,
        # sweep'ning "soxta sinish, rad etish" hikoyasi BEKOR bo'lganini
        # bildiradi (narx aslida teskari yo'nalishda davom etgan/kuchli
        # bo'lgan), FVG esa sweep bilan HECH QANDAY aloqasi yo'q, boshqa,
        # aloqasiz harakatning qismi bo'lishi mumkin.
        if direction == "bullish":
            invalidated = any(sub["low"].iloc[k] < last_sweep["sweep_area_bottom"]
                               for k in range(sweep_idx + 1, fvg_idx))
        else:
            invalidated = any(sub["high"].iloc[k] > last_sweep["sweep_area_top"]
                               for k in range(sweep_idx + 1, fvg_idx))
        if invalidated:
            reason = (f"eng so'nggi sweep @{sweep_idx} + FVG @{fvg_idx} - orada narx "
                      f"sweep zonasining qarama-qarshi chetidan chiqib ketgan - BEKOR")
            return None, reason

        if fvg_idx < cur - fresh_break_window + 1:
            reason = (f"eng so'nggi sweep @{sweep_idx} + FVG @{fvg_idx} - ESKIRGAN "
                      f"(cur={cur}, fresh_break_window={fresh_break_window})")
            return None, reason

        # MUHIM TUZATISH (SMC izchilligi, Jamshid tasdiqladi): FVG sweep
        # ZONASINING ichida (yoki undan yuqorida/pastida, yo'nalishga qarab)
        # bo'lishi kifoya — FVG zona ichida bo'lishi NORMAL (narx sweep'dan
        # keyin darhol ketmasdan, o'sha zonada biroz turib, keyin harakatga
        # o'tishi mumkin). Faqat FVG butun zonaning ENG CHETKI chegarasidan
        # (bullish uchun `sweep_area_bottom` — narx haqiqatda eng qancha
        # pastga tushgani, `pivot_level`dan farqli) TASHQARIDA bo'lsa —
        # bu, sweep bilan FVG orasida haqiqiy aloqa yo'qligini bildiradi,
        # rad etiladi. `sweep_area_top/bottom` allaqachon
        # `luxalgo_liquidity_sweeps.py`da hisoblab beriladi (LuxAlgo asl
        # mantig'idagi "Sweep Area"), shuning uchun u yerga tegilmadi —
        # faqat shu yerda, moslashtirish bosqichida ishlatilyapti.
        if direction == "bullish":
            if fvg["bottom"] < last_sweep["sweep_area_bottom"]:
                reason = (
                    f"eng so'nggi sweep @{sweep_idx} + FVG @{fvg_idx} - FVG sweep "
                    f"ZONASIDAN TASHQARIDA (fvg_bottom={fvg['bottom']:.2f} < "
                    f"sweep_area_bottom={last_sweep['sweep_area_bottom']:.2f})"
                )
                return None, reason
        else:  # bearish
            if fvg["top"] > last_sweep["sweep_area_top"]:
                reason = (
                    f"eng so'nggi sweep @{sweep_idx} + FVG @{fvg_idx} - FVG sweep "
                    f"ZONASIDAN TASHQARIDA (fvg_top={fvg['top']:.2f} > "
                    f"sweep_area_top={last_sweep['sweep_area_top']:.2f})"
                )
                return None, reason

        best = {
            "sweep_idx": sweep_idx,
            "sweep_level": last_sweep["pivot_level"],
            "sweep_kind": last_sweep["kind"],
            "fvg_idx": fvg_idx,
            "fvg_top": fvg["top"],
            "fvg_bottom": fvg["bottom"],
        }

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
