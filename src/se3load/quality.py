"""Data-quality checks and cleaning rules for the SE3 hourly dataset.

Rule of the project: nothing is removed silently. Every check returns flags or
counts that end up in reports/data_quality_summary.json and docs/data_quality.md.
The only values changed are (a) the eSett sign convention, (b) temperature readings
outside physical limits or isolated spikes, which are set to NaN, and (c) short
temperature gaps (<= MAX_TEMP_GAP_HOURS) that are linearly interpolated. All three
are flagged in the output.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import esett, smhi

LOCAL_TZ = "Europe/Stockholm"

# Temperature rules (see docs/decisions.md D8).
TEMP_MIN_C, TEMP_MAX_C = -45.0, 40.0
TEMP_SPIKE_C = 8.0  # |x - mean(prev, next)| above this, with both neighbours present
MAX_TEMP_GAP_HOURS = 3

# Load rules (D9): flagged, never removed.
LOAD_ROBUST_Z = 6.0
LOAD_STUCK_HOURS = 3


# ---------------------------------------------------------------- consumption

def hourly_load(raw: pd.DataFrame) -> pd.DataFrame:
    """Raw eSett rows (negative MWh per period) -> hourly load in MW with period counts."""
    hourly = esett.to_hourly(esett.to_positive_consumption(raw), value_kind="energy")
    return hourly


def expected_periods(timestamps: pd.Series, switch_utc: pd.Timestamp | None) -> pd.Series:
    """Settlement periods per hour: 1 before the 15-minute switch, 4 from it."""
    if switch_utc is None:
        return pd.Series(1, index=timestamps.index)
    return pd.Series(np.where(timestamps >= switch_utc, 4, 1), index=timestamps.index)


def detect_resolution_switch(raw: pd.DataFrame) -> pd.Timestamp | None:
    """First timestamp of the first 15-minute step, or None if the series is hourly."""
    ts = raw["timestamp_utc"].sort_values().reset_index(drop=True)
    step = ts.diff()
    quarter = step == pd.Timedelta(minutes=15)
    if not quarter.any():
        return None
    first = quarter.idxmax()  # first True
    return ts.iloc[first - 1]


def missing_hours(timestamps: pd.Series, start: str, end: str) -> pd.DatetimeIndex:
    full = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"),
                         freq="1h", inclusive="left")
    return full.difference(pd.DatetimeIndex(timestamps))


def robust_same_hour_z(load: pd.Series, weeks: int = 2) -> pd.Series:
    """Robust z-score vs the median of the same hour-of-week in surrounding weeks.

    Reference = median of load at t ± 168 h, ..., t ± weeks*168 h (excluding t).
    Scale = 1.4826 × median absolute residual over a centred 30-day window.
    Assumes a regular hourly index.
    """
    shifts = [k * 168 for k in range(1, weeks + 1)]
    neighbours = pd.concat([load.shift(s) for s in shifts] + [load.shift(-s) for s in shifts],
                           axis=1)
    ref = neighbours.median(axis=1, skipna=True)
    resid = load - ref
    scale = 1.4826 * resid.abs().rolling(30 * 24, center=True, min_periods=7 * 24).median()
    return resid / scale


def stuck_runs(values: pd.Series, min_len: int = LOAD_STUCK_HOURS) -> pd.Series:
    """True for values inside a run of >= min_len identical consecutive values."""
    change = values.ne(values.shift()).cumsum()
    run_len = values.groupby(change).transform("size")
    return (run_len >= min_len) & values.notna()


# ---------------------------------------------------------------- temperature

def clean_station_temperature(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Per station: set out-of-range values and isolated spikes to NaN (flagged).

    `df` is long format: timestamp_utc, station, temperature_c, quality, source.
    Returns the cleaned frame (with `temp_rule` column) and counts per rule.
    """
    out = df.sort_values(["station", "timestamp_utc"]).copy()
    out["temp_rule"] = ""
    out_of_range = (out["temperature_c"] < TEMP_MIN_C) | (out["temperature_c"] > TEMP_MAX_C)
    out.loc[out_of_range, "temp_rule"] = "out_of_range"
    out.loc[out_of_range, "temperature_c"] = np.nan

    spikes = pd.Series(False, index=out.index)
    for _, g in out.groupby("station"):
        s = g.set_index("timestamp_utc")["temperature_c"]
        prev_ = s.shift(1, freq="1h").reindex(s.index)
        next_ = s.shift(-1, freq="1h").reindex(s.index)
        dev = (s - (prev_ + next_) / 2).abs()
        is_spike = (dev > TEMP_SPIKE_C) & prev_.notna() & next_.notna()
        spikes.loc[g.index] = is_spike.to_numpy()
    out.loc[spikes, "temp_rule"] = "spike"
    out.loc[spikes, "temperature_c"] = np.nan
    counts = {"out_of_range": int(out_of_range.sum()), "spike": int(spikes.sum())}
    return out, counts


def hourly_temperature(clean_long: pd.DataFrame, weights: dict[str, float],
                       start: str, end: str) -> pd.DataFrame:
    """Population-weighted hourly temperature on a complete hourly index.

    Hours with no station are linearly interpolated if the gap is <= MAX_TEMP_GAP_HOURS
    (flag `temp_imputed`); longer gaps stay NaN.
    """
    w = smhi.weighted_temperature(clean_long, weights).set_index("timestamp_utc")
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"),
                        freq="1h", inclusive="left", name="timestamp_utc")
    w = w.reindex(idx)
    missing = w["temperature_c"].isna()
    filled = w["temperature_c"].interpolate(method="time", limit=MAX_TEMP_GAP_HOURS,
                                            limit_area="inside")
    # interpolate(limit=k) would partially fill longer gaps; only fill gaps of length <= k.
    gap_id = missing.ne(missing.shift()).cumsum()
    gap_len = missing.groupby(gap_id).transform("sum")
    fillable = missing & (gap_len <= MAX_TEMP_GAP_HOURS)
    w["temperature_c"] = w["temperature_c"].where(~fillable, filled)
    w["temp_imputed"] = fillable & w["temperature_c"].notna()
    w["weight_coverage"] = w["weight_coverage"].fillna(0.0)
    return w.reset_index()


def heating_degree_hours(temp_c: pd.Series, base_c: float = 17.0) -> float:
    """Sum of max(base - T, 0) over hours, a standard proxy for heating need."""
    return float(np.clip(base_c - temp_c, 0, None).sum())


# ---------------------------------------------------------------- assembly

def add_local_time(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    local = out["timestamp_utc"].dt.tz_convert(LOCAL_TZ)
    out["local_time"] = local.dt.strftime("%Y-%m-%d %H:%M%z")
    out["local_date"] = local.dt.date.astype(str)
    out["local_hour"] = local.dt.hour
    return out


def local_day_lengths(df: pd.DataFrame) -> pd.Series:
    """Number of hours per local calendar day (23/25 on DST change days)."""
    return df.groupby("local_date").size()


def period_shift_by_temperature(data: pd.DataFrame, before: tuple[str, str],
                                after: tuple[str, str],
                                bins: tuple[float, ...] = (-30, 0, 5, 10, 15, 30)) -> list[dict]:
    """Mean daily load (working days, no holidays) per temperature bin in two periods.

    A descriptive comparison at similar temperatures, not a causal estimate: other
    things also changed between the periods (prices, industry, population).
    """
    d = data.copy()
    d["date"] = pd.to_datetime(d["local_date"])
    daily = d.groupby("date").agg(load=("load_mw", "mean"), temp=("temperature_c", "mean"),
                                  hours=("load_mw", "size"),
                                  holiday=("is_holiday_or_eve", "max"))
    daily = daily[(daily["hours"] >= 23) & (~daily["holiday"]) & (daily.index.weekday < 5)]
    daily["bin"] = pd.cut(daily["temp"], bins=list(bins))
    rows = []
    for b, g in daily.groupby("bin", observed=True):
        p1, p2 = g.loc[before[0]:before[1], "load"], g.loc[after[0]:after[1], "load"]
        if len(p1) < 10 or len(p2) < 10:
            continue
        diff = p2.mean() - p1.mean()
        se = float(np.sqrt(p1.var(ddof=1) / len(p1) + p2.var(ddof=1) / len(p2)))
        rows.append({"temp_bin_c": str(b), "n_before": int(len(p1)), "n_after": int(len(p2)),
                     "mean_before_mw": round(float(p1.mean())), "mean_after_mw": round(float(p2.mean())),
                     "diff_mw": round(float(diff)), "diff_pct": round(float(100 * diff / p1.mean()), 1),
                     "ci95_mw": [round(float(diff - 1.96 * se)), round(float(diff + 1.96 * se))]})
    return rows
