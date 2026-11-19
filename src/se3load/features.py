"""Feature engineering that respects what is known at bid time.

Forecast setup (docs/decisions.md D4, D12):
- Issued at 12:00 local time on day D-1 for every hour of local day D.
- Known at issue time: the calendar for D; the temperature for D (observed values
  stand in for a perfect weather forecast, D4); zone load **up to the end of local
  day D-2** (D12).
- Load lags are therefore built from *local calendar days* (same local clock hour on
  D-2 and D-7), never from fixed UTC offsets: a 48-hour UTC lag points into D-1 for
  the last hour of the autumn DST day, which would leak data.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from . import calendar_se

HDD_BASE_C = 15.0   # heating degrees below this (load flattens above ~15 °C, M3 figure)
CDD_BASE_C = 20.0   # cooling degrees above this
LOAD_LAG_DAYS = (2, 7)
SUMMER_BREAK_ISO_WEEKS = (28, 29, 30, 31)  # Swedish industrial summer break (July)

REGIMES = ("pre_2022", "y2022", "post_2022")


def regime(local_date: pd.Series) -> pd.Series:
    d = pd.to_datetime(local_date)
    return pd.Series(np.select([d < "2022-01-01", d < "2023-01-01"], ["pre_2022", "y2022"],
                               "post_2022"), index=local_date.index)


def calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calendar features from local_date / local_hour. Known for any future day."""
    d = pd.to_datetime(df["local_date"])
    years = range(int(d.dt.year.min()) - 1, int(d.dt.year.max()) + 2)
    public = {k for y in years for k in calendar_se.public_holidays(y)}
    de_facto = {k for y in years for k in calendar_se.de_facto_holidays(y)}
    off = public | de_facto
    days = pd.Series(d.dt.date.unique())
    bridge = {x for x in days if calendar_se.is_bridge_day(x, off)}

    pydate = d.dt.date
    out = pd.DataFrame(index=df.index)
    out["hour"] = df["local_hour"].astype(int)
    out["dow"] = d.dt.dayofweek
    out["month"] = d.dt.month
    out["is_public_holiday"] = pydate.isin(public)
    out["is_de_facto_holiday"] = pydate.isin(de_facto)
    out["is_bridge_day"] = pydate.isin(bridge)
    out["is_weekend"] = out["dow"] >= 5
    # Day type for hour profiles: holidays behave like Sundays (M3 outlier screen).
    out["daytype"] = np.select(
        [out["is_public_holiday"] | out["is_de_facto_holiday"] | (out["dow"] == 6),
         out["dow"] == 5, out["is_bridge_day"], out["dow"] == 0, out["dow"] == 4],
        ["sunday_or_holiday", "saturday", "bridge", "monday", "friday"], "midweek")
    out["is_summer_break"] = d.dt.isocalendar().week.astype(int).isin(SUMMER_BREAK_ISO_WEEKS)
    md = d.dt.month * 100 + d.dt.day
    out["is_christmas_period"] = (md >= 1224) | (md <= 106)
    doy = d.dt.dayofyear.to_numpy()
    for k in (1, 2):
        out[f"year_sin{k}"] = np.sin(2 * np.pi * k * doy / 365.25)
        out[f"year_cos{k}"] = np.cos(2 * np.pi * k * doy / 365.25)
    out["regime"] = regime(df["local_date"])
    out["years_since_2019"] = (d - pd.Timestamp("2019-01-01")).dt.days / 365.25
    return out


def temperature_features(df: pd.DataFrame) -> pd.DataFrame:
    """Temperature-based features. Uses the temperature path up to the target hour,
    which is either observed (before issue time) or 'forecast' (D4)."""
    t = df.set_index("timestamp_utc")["temperature_c"]
    out = pd.DataFrame(index=df.index)
    out["temp"] = t.to_numpy()
    out["hdd"] = np.clip(HDD_BASE_C - out["temp"], 0, None)
    out["cdd"] = np.clip(out["temp"] - CDD_BASE_C, 0, None)
    # Buildings react with a lag: exponentially smoothed temperature (half-life 12 h / 48 h).
    for hl in (12, 48):
        out[f"temp_ewm{hl}"] = t.ewm(halflife=hl, adjust=False).mean().to_numpy()
    out["hdd_ewm48"] = np.clip(HDD_BASE_C - out["temp_ewm48"], 0, None)
    out["temp_daily_mean"] = df.groupby("local_date")["temperature_c"].transform("mean").to_numpy()
    return out


def load_lag_features(df: pd.DataFrame, lag_days: tuple[int, ...] = LOAD_LAG_DAYS) -> pd.DataFrame:
    """Same local clock hour on D-k, plus the daily mean of D-k.

    On the autumn DST day the repeated hour is averaged; on the spring DST day the
    missing 02:00 hour falls back to 03:00 of the same day.
    """
    key = df[["local_date", "local_hour", "load_mw"]].copy()
    key["local_date"] = pd.to_datetime(key["local_date"])
    by_hour = key.groupby(["local_date", "local_hour"])["load_mw"].mean()
    daily = key.groupby("local_date")["load_mw"].mean()
    target_date = pd.to_datetime(df["local_date"])
    out = pd.DataFrame(index=df.index)
    for k in lag_days:
        src_date = target_date - pd.Timedelta(days=k)
        idx = pd.MultiIndex.from_arrays([src_date, df["local_hour"]])
        vals = by_hour.reindex(idx).to_numpy()
        missing = np.isnan(vals)
        if missing.any():
            alt = by_hour.reindex(pd.MultiIndex.from_arrays(
                [src_date, df["local_hour"] + 1])).to_numpy()
            vals = np.where(missing, alt, vals)
        out[f"load_d{k}_same_hour"] = vals
        out[f"load_d{k}_daily_mean"] = daily.reindex(src_date).to_numpy()
    return out


def lag_source_dates(target_local_date: date, lag_days: tuple[int, ...] = LOAD_LAG_DAYS) -> list[date]:
    """Local dates whose load a forecast for `target_local_date` may read."""
    return [target_local_date - pd.Timedelta(days=k) for k in lag_days]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """All features for every hour in `df` (the processed hourly dataset)."""
    df = df.reset_index(drop=True)
    parts = [df[["timestamp_utc", "local_date", "local_hour", "load_mw"]],
             calendar_features(df), temperature_features(df), load_lag_features(df)]
    return pd.concat(parts, axis=1)


# ------------------------------------------------------------- design matrix

NUMERIC = ["temp", "hdd", "cdd", "temp_ewm12", "temp_ewm48", "hdd_ewm48", "temp_daily_mean",
           "year_sin1", "year_cos1", "year_sin2", "year_cos2",
           "load_d2_same_hour", "load_d2_daily_mean", "load_d7_same_hour", "load_d7_daily_mean"]
FLAGS = ["is_public_holiday", "is_de_facto_holiday", "is_bridge_day", "is_summer_break",
         "is_christmas_period"]


def linear_design(feat: pd.DataFrame, regime_terms: bool = False,
                  use_lags: bool = True) -> pd.DataFrame:
    """Design matrix for linear models: hour×daytype profile, hour-specific heating
    slope, flags and numeric terms. Optional regime level and regime×hdd terms."""
    num = [c for c in NUMERIC if use_lags or not c.startswith("load_")]
    X = feat[num].astype(float).copy()
    for c in FLAGS:
        X[c] = feat[c].astype(float)
    prof = pd.get_dummies(feat["daytype"] + "_h" + feat["hour"].astype(str), dtype=float)
    X = pd.concat([X, prof], axis=1)
    hours = pd.get_dummies(feat["hour"], prefix="hdd_h", dtype=float)
    X = pd.concat([X, hours.mul(feat["hdd"], axis=0)], axis=1)
    if regime_terms:
        for r in REGIMES[1:]:
            ind = (feat["regime"] == r).astype(float)
            X[f"regime_{r}"] = ind
            X[f"regime_{r}_x_hdd"] = ind * feat["hdd"]
    return X
