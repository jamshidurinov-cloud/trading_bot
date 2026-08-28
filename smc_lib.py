"""
smc_lib.py

joshyattridge/smart-money-concepts kutubxonasining nusxasi (PyPI: smartmoneyconcepts).
Bu — LuxAlgo'ning ochiq manba Pine Script SMC indikatoridan Python'ga portlangan,
keng qo'llaniladigan, sinalgan kod.

Manba: https://github.com/joshyattridge/smart-money-concepts
Litsenziya: ochiq manba (repo'da ko'rsatilgan)

PYPI orqali o'rnatiladi (requirements.txt): smartmoneyconcepts
Bu fayl faqat SANDBOX'da sinash uchun saqlangan (tarmoq cheklangani sabab pip install
ishlamadi) - Render'da esa requirements.txt orqali to'g'ridan-to'g'ri o'rnatiladi.
"""

from functools import wraps
import pandas as pd
import numpy as np
from sweep_lib import detect_sweep_events
from pandas import DataFrame, Series
from datetime import datetime


def inputvalidator(input_="ohlc"):
    def dfcheck(func):
        @wraps(func)
        def wrap(*args, **kwargs):
            args = list(args)
            i = 0 if isinstance(args[0], pd.DataFrame) else 1
            args[i] = args[i].rename(columns={c: c.lower() for c in args[i].columns})
            inputs = {
                "o": "open",
                "h": "high",
                "l": "low",
                "c": kwargs.get("column", "close").lower(),
                "v": "volume",
            }
            if inputs["c"] != "close":
                kwargs["column"] = inputs["c"]
            for l in input_:
                if inputs[l] not in args[i].columns:
                    raise LookupError(
                        'Must have a dataframe column named "{0}"'.format(inputs[l])
                    )
            return func(*args, **kwargs)

        return wrap

    return dfcheck


def apply(decorator):
    def decorate(cls):
        for attr in cls.__dict__:
            if callable(getattr(cls, attr)):
                setattr(cls, attr, decorator(getattr(cls, attr)))
        return cls

    return decorate


@apply(inputvalidator(input_="ohlc"))
class smc:
    __version__ = "0.0.27"

    @classmethod
    def fvg(cls, ohlc: DataFrame, join_consecutive=False) -> Series:
        fvg = np.where(
            (
                (ohlc["high"].shift(1) < ohlc["low"].shift(-1))
                & (ohlc["close"] > ohlc["open"])
            )
            | (
                (ohlc["low"].shift(1) > ohlc["high"].shift(-1))
                & (ohlc["close"] < ohlc["open"])
            ),
            np.where(ohlc["close"] > ohlc["open"], 1, -1),
            np.nan,
        )

        top = np.where(
            ~np.isnan(fvg),
            np.where(
                ohlc["close"] > ohlc["open"],
                ohlc["low"].shift(-1),
                ohlc["low"].shift(1),
            ),
            np.nan,
        )

        bottom = np.where(
            ~np.isnan(fvg),
            np.where(
                ohlc["close"] > ohlc["open"],
                ohlc["high"].shift(1),
                ohlc["high"].shift(-1),
            ),
            np.nan,
        )

        if join_consecutive:
            for i in range(len(fvg) - 1):
                if fvg[i] == fvg[i + 1]:
                    top[i + 1] = max(top[i], top[i + 1])
                    bottom[i + 1] = min(bottom[i], bottom[i + 1])
                    fvg[i] = top[i] = bottom[i] = np.nan

        mitigated_index = np.zeros(len(ohlc), dtype=np.int32)
        for i in np.where(~np.isnan(fvg))[0]:
            mask = np.zeros(len(ohlc), dtype=np.bool_)
            if fvg[i] == 1:
                mask = ohlc["low"][i + 2:] <= top[i]
            elif fvg[i] == -1:
                mask = ohlc["high"][i + 2:] >= bottom[i]
            if np.any(mask):
                j = np.argmax(mask) + i + 2
                mitigated_index[i] = j

        mitigated_index = np.where(np.isnan(fvg), np.nan, mitigated_index)

        return pd.concat(
            [
                pd.Series(fvg, name="FVG"),
                pd.Series(top, name="Top"),
                pd.Series(bottom, name="Bottom"),
                pd.Series(mitigated_index, name="MitigatedIndex"),
            ],
            axis=1,
        )

    @classmethod
    def swing_highs_lows(cls, ohlc: DataFrame, swing_length: int = 50) -> Series:
        swing_length *= 2
        swing_highs_lows = np.where(
            ohlc["high"]
            == ohlc["high"].shift(-(swing_length // 2)).rolling(swing_length).max(),
            1,
            np.where(
                ohlc["low"]
                == ohlc["low"].shift(-(swing_length // 2)).rolling(swing_length).min(),
                -1,
                np.nan,
            ),
        )

        while True:
            positions = np.where(~np.isnan(swing_highs_lows))[0]
            if len(positions) < 2:
                break
            current = swing_highs_lows[positions[:-1]]
            next = swing_highs_lows[positions[1:]]
            highs = ohlc["high"].iloc[positions[:-1]].values
            lows = ohlc["low"].iloc[positions[:-1]].values
            next_highs = ohlc["high"].iloc[positions[1:]].values
            next_lows = ohlc["low"].iloc[positions[1:]].values

            index_to_remove = np.zeros(len(positions), dtype=bool)

            consecutive_highs = (current == 1) & (next == 1)
            index_to_remove[:-1] |= consecutive_highs & (highs < next_highs)
            index_to_remove[1:] |= consecutive_highs & (highs >= next_highs)

            consecutive_lows = (current == -1) & (next == -1)
            index_to_remove[:-1] |= consecutive_lows & (lows > next_lows)
            index_to_remove[1:] |= consecutive_lows & (lows <= next_lows)

            if not index_to_remove.any():
                break

            swing_highs_lows[positions[index_to_remove]] = np.nan

        positions = np.where(~np.isnan(swing_highs_lows))[0]

        if len(positions) > 0:
            if swing_highs_lows[positions[0]] == 1:
                swing_highs_lows[0] = -1
            if swing_highs_lows[positions[0]] == -1:
                swing_highs_lows[0] = 1
            if swing_highs_lows[positions[-1]] == -1:
                swing_highs_lows[-1] = 1
            if swing_highs_lows[positions[-1]] == 1:
                swing_highs_lows[-1] = -1

        level = np.where(
            ~np.isnan(swing_highs_lows),
            np.where(swing_highs_lows == 1, ohlc["high"], ohlc["low"]),
            np.nan,
        )

        return pd.concat(
            [
                pd.Series(swing_highs_lows, name="HighLow"),
                pd.Series(level, name="Level"),
            ],
            axis=1,
        )

    @classmethod
    def bos_choch(
        cls, ohlc: DataFrame, swing_highs_lows: DataFrame, close_break: bool = True
    ) -> Series:
        swing_highs_lows = swing_highs_lows.copy()

        level_order = []
        highs_lows_order = []

        bos = np.zeros(len(ohlc), dtype=np.int32)
        choch = np.zeros(len(ohlc), dtype=np.int32)
        level = np.zeros(len(ohlc), dtype=np.float32)

        last_positions = []

        for i in range(len(swing_highs_lows["HighLow"])):
            if not np.isnan(swing_highs_lows["HighLow"][i]):
                level_order.append(swing_highs_lows["Level"][i])
                highs_lows_order.append(swing_highs_lows["HighLow"][i])
                if len(level_order) >= 4:
                    bos[last_positions[-2]] = (
                        1
                        if (
                            np.all(highs_lows_order[-4:] == [-1, 1, -1, 1])
                            and np.all(
                                level_order[-4]
                                < level_order[-2]
                                < level_order[-3]
                                < level_order[-1]
                            )
                        )
                        else 0
                    )
                    level[last_positions[-2]] = (
                        level_order[-3] if bos[last_positions[-2]] != 0 else 0
                    )

                    bos[last_positions[-2]] = (
                        -1
                        if (
                            np.all(highs_lows_order[-4:] == [1, -1, 1, -1])
                            and np.all(
                                level_order[-4]
                                > level_order[-2]
                                > level_order[-3]
                                > level_order[-1]
                            )
                        )
                        else bos[last_positions[-2]]
                    )
                    level[last_positions[-2]] = (
                        level_order[-3] if bos[last_positions[-2]] != 0 else 0
                    )

                    choch[last_positions[-2]] = (
                        1
                        if (
                            np.all(highs_lows_order[-4:] == [-1, 1, -1, 1])
                            and np.all(
                                level_order[-1]
                                > level_order[-3]
                                > level_order[-4]
                                > level_order[-2]
                            )
                        )
                        else 0
                    )
                    level[last_positions[-2]] = (
                        level_order[-3]
                        if choch[last_positions[-2]] != 0
                        else level[last_positions[-2]]
                    )

                    choch[last_positions[-2]] = (
                        -1
                        if (
                            np.all(highs_lows_order[-4:] == [1, -1, 1, -1])
                            and np.all(
                                level_order[-1]
                                < level_order[-3]
                                < level_order[-4]
                                < level_order[-2]
                            )
                        )
                        else choch[last_positions[-2]]
                    )
                    level[last_positions[-2]] = (
                        level_order[-3]
                        if choch[last_positions[-2]] != 0
                        else level[last_positions[-2]]
                    )

                last_positions.append(i)

        broken = np.zeros(len(ohlc), dtype=np.int32)
        for i in np.where(np.logical_or(bos != 0, choch != 0))[0]:
            mask = np.zeros(len(ohlc), dtype=np.bool_)
            if bos[i] == 1 or choch[i] == 1:
                mask = ohlc["close" if close_break else "high"][i + 2:] > level[i]
            elif bos[i] == -1 or choch[i] == -1:
                mask = ohlc["close" if close_break else "low"][i + 2:] < level[i]

            if np.any(mask):
                j = np.argmax(mask) + i + 2
                broken[i] = j
                for k in np.where(np.logical_or(bos != 0, choch != 0))[0]:
                    if k < i and broken[k] >= j:
                        bos[k] = 0
                        choch[k] = 0
                        level[k] = 0

        for i in np.where(
            np.logical_and(np.logical_or(bos != 0, choch != 0), broken == 0)
        )[0]:
            bos[i] = 0
            choch[i] = 0
            level[i] = 0

        bos = np.where(bos != 0, bos, np.nan)
        choch = np.where(choch != 0, choch, np.nan)
        level = np.where(level != 0, level, np.nan)
        broken = np.where(broken != 0, broken, np.nan)

        bos = pd.Series(bos, name="BOS")
        choch = pd.Series(choch, name="CHOCH")
        level = pd.Series(level, name="Level")
        broken = pd.Series(broken, name="BrokenIndex")

        return pd.concat([bos, choch, level, broken], axis=1)

    @classmethod
    def liquidity(cls, ohlc: DataFrame, swing_highs_lows: DataFrame, range_percent: float = 0.01) -> Series:
        """Liquidity - bir-biriga yaqin (teng) cho'qqilar yoki tublar to'plami.
        Liquidity = 1 agar teng cho'qqilar (keyin pastga sweep bo'lsa bearish setup),
        Liquidity = -1 agar teng tublar (keyin yuqoriga sweep bo'lsa bullish setup).
        Swept = shu likvidlikni "tozalagan" (sweep qilgan) svecha indeksi."""
        shl = swing_highs_lows.copy()
        n = len(ohlc)
        pip_range = (ohlc["high"].max() - ohlc["low"].min()) * range_percent
        ohlc_high = ohlc["high"].values
        ohlc_low = ohlc["low"].values
        shl_HL = shl["HighLow"].values.copy()
        shl_Level = shl["Level"].values.copy()

        liquidity = np.full(n, np.nan, dtype=np.float32)
        liquidity_level = np.full(n, np.nan, dtype=np.float32)
        liquidity_end = np.full(n, np.nan, dtype=np.float32)
        liquidity_swept = np.full(n, np.nan, dtype=np.float32)

        bull_indices = np.nonzero(shl_HL == 1)[0]
        for i in bull_indices:
            if shl_HL[i] != 1:
                continue
            high_level = shl_Level[i]
            range_low = high_level - pip_range
            range_high = high_level + pip_range
            group_levels = [high_level]
            group_end = i
            c_start = i + 1
            if c_start < n:
                cond = ohlc_high[c_start:] >= range_high
                swept = c_start + int(np.argmax(cond)) if np.any(cond) else 0
            else:
                swept = 0
            for j in bull_indices:
                if j <= i:
                    continue
                if swept and j >= swept:
                    break
                if shl_HL[j] == 1 and (range_low <= shl_Level[j] <= range_high):
                    group_levels.append(shl_Level[j])
                    group_end = j
                    shl_HL[j] = 0
            if len(group_levels) > 1:
                avg_level = sum(group_levels) / len(group_levels)
                liquidity[i] = 1
                liquidity_level[i] = avg_level
                liquidity_end[i] = group_end
                liquidity_swept[i] = swept

        bear_indices = np.nonzero(shl_HL == -1)[0]
        for i in bear_indices:
            if shl_HL[i] != -1:
                continue
            low_level = shl_Level[i]
            range_low = low_level - pip_range
            range_high = low_level + pip_range
            group_levels = [low_level]
            group_end = i
            c_start = i + 1
            if c_start < n:
                cond = ohlc_low[c_start:] <= range_low
                swept = c_start + int(np.argmax(cond)) if np.any(cond) else 0
            else:
                swept = 0
            for j in bear_indices:
                if j <= i:
                    continue
                if swept and j >= swept:
                    break
                if shl_HL[j] == -1 and (range_low <= shl_Level[j] <= range_high):
                    group_levels.append(shl_Level[j])
                    group_end = j
                    shl_HL[j] = 0
            if len(group_levels) > 1:
                avg_level = sum(group_levels) / len(group_levels)
                liquidity[i] = -1
                liquidity_level[i] = avg_level
                liquidity_end[i] = group_end
                liquidity_swept[i] = swept

        return pd.concat(
            [
                pd.Series(liquidity, name="Liquidity"),
                pd.Series(liquidity_level, name="Level"),
                pd.Series(liquidity_end, name="End"),
                pd.Series(liquidity_swept, name="Swept"),
            ],
            axis=1,
        )


def detect_smc_official_signal(df, lookback=144, swing_length=6, fresh_break_window=5,
                                 min_fvg_mult=0.1, range_percent=0.01):
    """smartmoneyconcepts kutubxonasi (LuxAlgo'dan portlangan) asosida signal aniqlaydi.

    MAJBURIY komponentlar (ikkalasi ham bo'lishi shart):
    1. Sweep (Liquidity) — teng cho'qqi/tub to'plami "tozalangan" (sweep qilingan)
    2. FVG — sweep'dan keyin hosil bo'lgan Fair Value Gap

    QO'SHIMCHA (bo'lsa ham, bo'lmasa ham signal chiqadi, lekin bo'lsa "kuchliroq"
    deb belgilanadi):
    3. BOS/CHoCH — struktura sinishi (kutubxonaning aniq pattern-matching orqali
       BOS/CHoCH farqini ajratuvchi funksiyasi)
    """
    sub = df.iloc[-lookback:].copy()
    if len(sub) < lookback:
        return None

    n = len(sub)
    cur = n - 1
    times = sub.index

    swings = smc.swing_highs_lows(sub, swing_length=swing_length)
    fvg_df = smc.fvg(sub, join_consecutive=False)
    bc = smc.bos_choch(sub, swings, close_break=True)

    # Sweep endi sweep_lib.py ("Liquidity Sweep v2" g'oyasidan) orqali topiladi:
    # faqat TRAP (narx darajani buzib, QAYTIB yopilgan) haqiqiy sweep hisoblanadi.
    # BREAK (narx darajani buzib, o'sha tomonda davom etgan) - bu sweep EMAS,
    # oddiy struktura sinishi/trend davomiyligi, shuning uchun rad etiladi.
    sweep_events = detect_sweep_events(sub, swing_window=swing_length, expiry_bars=lookback, confirmation="close")
    traps = [e for e in sweep_events if e["type"] == "trap"]

    avg_range = (sub["high"] - sub["low"]).mean()
    if avg_range == 0:
        return None
    min_fvg_size = avg_range * min_fvg_mult

    def find_signal(direction):
        # bullish uchun: PAST tomondagi trap (support sweep, qaytib yopilgan) -> keyin bullish FVG
        # bearish uchun: YUQORI tomondagi trap (resistance sweep, qaytib yopilgan) -> keyin bearish FVG
        fvg_type = 1 if direction == "bullish" else -1

        candidates = [e for e in traps if e["direction"] == direction]
        if not candidates:
            return None, f"{direction}: hech qanday TRAP (haqiqiy sweep) topilmadi"

        best = None
        rejected_reasons = []
        for trap in candidates:
            swept_idx = trap["event_idx"]
            fvg_candidates = fvg_df[
                (fvg_df["FVG"] == fvg_type)
                & (fvg_df.index > swept_idx)
                & ((fvg_df["Top"] - fvg_df["Bottom"]).abs() >= min_fvg_size)
            ]
            if fvg_candidates.empty:
                rejected_reasons.append(
                    f"sweep@{swept_idx}(lvl={trap['level']:.2f}) - keyin mos FVG topilmadi"
                )
                continue
            fvg_idx = fvg_candidates.index[-1]  # eng so'nggi mos FVG

            # "yangilik" tekshiruvi: FVG (yoki undan keyingi tasdiqlash) joriy vaqtga yaqin bo'lishi kerak
            if fvg_idx < cur - fresh_break_window + 1:
                rejected_reasons.append(
                    f"sweep@{swept_idx} + FVG@{fvg_idx} topildi, lekin ESKI "
                    f"(cur={cur}, fresh_break_window={fresh_break_window})"
                )
                continue

            # Tanlov mezoni: avval FVG yangiligi (kattaroq fvg_idx), TENG bo'lsa
            # esa eng SO'NGGI (eng yaqin) sweep'ni afzal ko'ramiz - eski, unutilgan
            # sweep o'rniga yangi, dolzarbroq sweep ko'rsatilishi uchun
            is_better = (
                best is None
                or fvg_idx > best["_fvg_idx"]
                or (fvg_idx == best["_fvg_idx"] and swept_idx > best["sweep_idx"])
            )
            if is_better:
                fvg_row = fvg_candidates.loc[fvg_idx]
                best = {
                    "sweep_idx": swept_idx,
                    "sweep_level": float(trap["level"]),
                    "fvg_idx": fvg_idx,
                    "fvg_top": float(fvg_row["Top"]),
                    "fvg_bottom": float(fvg_row["Bottom"]),
                    "_fvg_idx": fvg_idx,
                }

        if best is None:
            reason = f"{direction}: {len(candidates)} ta sweep topildi, lekin hech biri fresh FVG bilan mos kelmadi. " \
                     + " | ".join(rejected_reasons[-3:])
            return None, reason

        # BOS/CHoCH - QO'SHIMCHA (majburiy emas): shu sweep va joriy vaqt oralig'ida bormi tekshiramiz
        has_structure = False
        structure_kind = None
        bc_matches = bc[(bc["BOS"].notna()) | (bc["CHOCH"].notna())]
        for bidx, brow in bc_matches.iterrows():
            broken_idx = brow["BrokenIndex"]
            if pd.isna(broken_idx):
                continue
            val = brow["BOS"] if not pd.isna(brow["BOS"]) else brow["CHOCH"]
            expected = 1 if direction == "bullish" else -1
            if val == expected and best["sweep_idx"] <= bidx <= cur and broken_idx <= cur:
                has_structure = True
                structure_kind = "BOS" if not pd.isna(brow["BOS"]) else "CHOCH"

        return {
            "type": f"smc_official_{direction}",
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
        print(f"[SMC_OFFICIAL DEBUG] {bull_reason} || {bear_reason}")

    # Ikkalasi ham topilsa (kamdan-kam), FVG yangiroq bo'lganini tanlaymiz
    if bullish_signal and bearish_signal:
        return bullish_signal if bullish_signal["fvg_time"] >= bearish_signal["fvg_time"] else bearish_signal
    return bullish_signal or bearish_signal
