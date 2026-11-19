"""Temperature sensitivity of SE3 load: MW per °C of cold, with HAC standard errors.

Estimated on observed data, not inside the forecast backtest, and *without* load lags:
lags would absorb most of the temperature effect and bias the slope towards zero.
The estimate is descriptive (association conditional on calendar effects), not a
causal effect of temperature; for heating demand the physics makes the direction
uncontroversial, but the size depends on what else co-moves with cold spells.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import features


def daily_frame(feat: pd.DataFrame) -> pd.DataFrame:
    """One row per complete local day: mean load, mean temperature, calendar."""
    g = feat.groupby("local_date").agg(
        load=("load_mw", "mean"), temp=("temp", "mean"), hours=("load_mw", "size"),
        dow=("dow", "first"), month=("month", "first"), daytype=("daytype", "first"),
        is_summer_break=("is_summer_break", "first"),
        is_christmas_period=("is_christmas_period", "first"),
        is_bridge_day=("is_bridge_day", "first"))
    g = g[g["hours"] >= 23].copy()
    g.index = pd.to_datetime(g.index)
    g["hdd"] = np.clip(features.HDD_BASE_C - g["temp"], 0, None)
    g["cdd"] = np.clip(g["temp"] - features.CDD_BASE_C, 0, None)
    return g


def daily_sensitivity(feat: pd.DataFrame, start: str, end: str, hac_lags: int = 14) -> dict:
    """OLS: daily mean load ~ HDD + CDD + daytype + month + flags, Newey-West SEs.

    The HDD coefficient is the extra average MW per °C that the daily mean temperature
    is below 15 °C.
    """
    import statsmodels.formula.api as smf

    d = daily_frame(feat).loc[start:end]
    d = d.assign(**{c: d[c].astype(int) for c in
                    ("is_summer_break", "is_christmas_period", "is_bridge_day")})
    model = smf.ols("load ~ hdd + cdd + C(daytype) + C(month) + is_summer_break"
                    " + is_christmas_period", data=d)
    res = model.fit(cov_type="HAC", cov_kwds={"maxlags": hac_lags})
    ols_se = model.fit().bse["hdd"]
    lo, hi = res.conf_int().loc["hdd"]
    return {"window": [start, end], "n_days": int(res.nobs),
            "mw_per_degc_below_15": round(float(res.params["hdd"]), 1),
            "ci95": [round(float(lo), 1), round(float(hi), 1)],
            "hac_se": round(float(res.bse["hdd"]), 2), "naive_ols_se": round(float(ols_se), 2),
            "hac_lags_days": hac_lags, "r2": round(float(res.rsquared), 3),
            "gwh_per_day_per_degc": round(float(res.params["hdd"]) * 24 / 1000, 2)}


def hourly_slopes(feat: pd.DataFrame, start: str, end: str, hac_lags: int = 168) -> dict:
    """Hour-specific heating slopes (MW per °C below 15 °C) with Newey-West SEs.

    Design: hour×daytype profile, month dummies, calendar flags, CDD and one HDD slope
    per local hour. No other temperature terms, so each slope is the full
    same-hour temperature association at that hour.
    """
    import statsmodels.api as sm

    dates = pd.to_datetime(feat["local_date"])
    f = feat[(dates >= start) & (dates <= end)].reset_index(drop=True)
    prof = pd.get_dummies(f["daytype"] + "_h" + f["hour"].astype(str), dtype=float)
    months = pd.get_dummies(f["month"], prefix="m", drop_first=True, dtype=float)
    flags = f[["is_summer_break", "is_christmas_period"]].astype(float)
    slopes = pd.get_dummies(f["hour"], prefix="hdd_h", dtype=float).mul(f["hdd"], axis=0)
    X = pd.concat([prof, months, flags, f[["cdd"]], slopes], axis=1)
    X = X.loc[:, X.std() > 0]
    res = sm.OLS(f["load_mw"].to_numpy(), X.to_numpy()).fit(
        cov_type="HAC", cov_kwds={"maxlags": hac_lags})
    ci = res.conf_int()
    cols = list(X.columns)
    out = {}
    for h in range(24):
        name = f"hdd_h_{h}"
        if name in cols:
            i = cols.index(name)
            out[h] = {"slope": round(float(res.params[i]), 1),
                      "ci95": [round(float(ci[i][0]), 1), round(float(ci[i][1]), 1)]}
    return out
