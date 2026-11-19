"""Exploration: multi-seasonal decomposition of daily load (needs statsmodels)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def daily_load(data: pd.DataFrame) -> pd.Series:
    """Daily mean load (MW) on complete local days (>= 23 hours), regular daily index."""
    g = data.groupby("local_date")["load_mw"].agg(["mean", "size"])
    g = g[g["size"] >= 23]["mean"]
    g.index = pd.to_datetime(g.index)
    return g.asfreq("D")


def mstl_daily(daily: pd.Series, periods: tuple[int, ...] = (7, 365)) -> pd.DataFrame:
    from statsmodels.tsa.seasonal import MSTL

    y = daily.interpolate(limit=2)
    res = MSTL(y, periods=periods, stl_kwargs={"robust": True}).fit()
    out = pd.DataFrame({"observed": res.observed, "trend": res.trend, "resid": res.resid})
    seas = res.seasonal if isinstance(res.seasonal, pd.DataFrame) else res.seasonal.to_frame()
    for col, p in zip(seas.columns, periods):
        out[f"seasonal_{p}"] = seas[col]
    return out


def seasonal_strength(dec: pd.DataFrame, component: str) -> float:
    """Wang, Smith & Hyndman (2006): max(0, 1 - Var(R) / Var(S + R))."""
    r = dec["resid"]
    return float(max(0.0, 1 - np.var(r) / np.var(dec[component] + r)))


def trend_strength(dec: pd.DataFrame) -> float:
    r = dec["resid"]
    return float(max(0.0, 1 - np.var(r) / np.var(dec["trend"] + r)))
