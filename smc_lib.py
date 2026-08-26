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


def detect_smc_official_signal(df, lookback=144, swing_length=6, fresh_break_window=3, min_fvg_mult=0.1):
    """smartmoneyconcepts kutubxonasi (LuxAlgo portlangan) asosida BOS/CHoCH + FVG
    signalini aniqlaydi. Kutubxonaning o'zi BOS (trend davomiyligi) va CHoCH
    (teskari burilish)ni aniq, pattern-matching orqali ajratadi."""
    sub = df.iloc[-lookback:].copy()
    if len(sub) < lookback:
        return None

    swings = smc.swing_highs_lows(sub, swing_length=swing_length)
    bc = smc.bos_choch(sub, swings, close_break=True)
    fvg_df = smc.fvg(sub, join_consecutive=False)

    n = len(sub)
    cur = n - 1
    times = sub.index

    broken_mask = bc["BrokenIndex"].notna()
    if not broken_mask.any():
        return None

    candidates = bc[broken_mask]
    recent = candidates[
        (candidates["BrokenIndex"] >= cur - fresh_break_window + 1)
        & (candidates["BrokenIndex"] <= cur)
    ]
    if recent.empty:
        return None

    recent_sorted = recent.sort_values("BrokenIndex")
    row = recent_sorted.iloc[-1]
    structure_idx = recent_sorted.index[-1]

    is_bos = not pd.isna(row["BOS"])
    direction = "bullish" if (row["BOS"] == 1 or row["CHOCH"] == 1) else "bearish"
    kind = "BOS" if is_bos else "CHOCH"
    level = row["Level"]
    broken_idx = int(row["BrokenIndex"])

    avg_range = (sub["high"] - sub["low"]).mean()
    min_fvg_size = avg_range * min_fvg_mult
    fvg_direction = 1 if direction == "bullish" else -1
    fvg_candidates = fvg_df[
        (fvg_df["FVG"] == fvg_direction)
        & ((fvg_df["Top"] - fvg_df["Bottom"]).abs() >= min_fvg_size)
    ]
    relevant_fvg = fvg_candidates[
        (fvg_candidates.index >= structure_idx) & (fvg_candidates.index <= broken_idx + 2)
    ]

    fvg_time = fvg_top = fvg_bottom = None
    if not relevant_fvg.empty:
        fvg_row = relevant_fvg.iloc[-1]
        fvg_idx = relevant_fvg.index[-1]
        fvg_time = str(times[fvg_idx])
        fvg_top = float(fvg_row["Top"])
        fvg_bottom = float(fvg_row["Bottom"])

    return {
        "type": f"smc_official_{direction}",
        "kind": kind,
        "structure_time": str(times[structure_idx]),
        "level": float(level),
        "broken_time": str(times[broken_idx]),
        "fvg_time": fvg_time,
        "fvg_top": fvg_top,
        "fvg_bottom": fvg_bottom,
        "current_close": float(sub["close"].iloc[cur]),
    }
