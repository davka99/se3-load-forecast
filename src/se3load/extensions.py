"""Post-backtest extensions (D20, D21), added after the pre-registered evaluation in D19.

Both extensions only use information that would have been available at the 12:00 issue
time on D-1, but they are evaluated on the same held-out year as D19. They are therefore
*not* an untouched test, and the README says so.

D20  Real archived day-ahead temperature forecasts (Open-Meteo) replace observed
     temperature in the prediction inputs, as the simulation in D19 approximated.
D21  Online recalibration of the 80 % interval and a peak-hour margin, learned only
     from residuals of days whose load was known at issue time (<= D-2).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import features, metrics, smhi

# ---------------------------------------------------------------- D20: forecasts

BIAS_WINDOW_DAYS = 28
KNOWN_LAG_DAYS = 2  # observed temperature and load are known up to D-2 at issue time


def weighted_forecast(fc_long: pd.DataFrame, weights: dict[str, float], lead_day: int,
                      ) -> pd.Series:
    """Population-weighted forecast temperature per UTC hour for one lead time."""
    d = fc_long[fc_long["lead_day"] == lead_day][["timestamp_utc", "station", "temperature_c"]]
    w = smhi.weighted_temperature(d, weights)
    return w.set_index("timestamp_utc")["temperature_c"].sort_index()


def bias_corrected(forecast: pd.Series, observed: pd.Series,
                   window_days: int = BIAS_WINDOW_DAYS,
                   known_lag_days: int = KNOWN_LAG_DAYS) -> tuple[pd.Series, pd.Series]:
    """Subtract the mean forecast error of the same UTC hour over the previous
    `window_days` days whose observations were known at issue time.

    For hour h on day D the correction uses days D-known_lag_days-window_days+1 ..
    D-known_lag_days, so nothing observed after the issue time leaks in. Hours without
    a full window (the warm-up) are left uncorrected; the second return value flags the
    hours that were corrected.
    """
    err = (forecast - observed.reindex(forecast.index)).rename("err").to_frame()
    err["hour"] = err.index.hour
    err["day"] = err.index.floor("D")
    corr = pd.Series(np.nan, index=forecast.index)
    for _, g in err.groupby("hour"):
        s = g.set_index("day")["err"]
        s = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D"))
        b = s.rolling(window_days, min_periods=window_days).mean().shift(known_lag_days)
        corr.loc[g.index] = b.reindex(g["day"]).to_numpy()
    return (forecast - corr.fillna(0.0)).rename(forecast.name), corr.notna()


def forecast_feature_sets(data: pd.DataFrame, variants: dict[str, pd.Series], start: str,
                          ) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    """Feature frames where temperature from `start` onwards is a forecast.

    Same construction as the D19 noise simulation (evaluate.noisy_feature_sets): history
    before `start` and all load values are unchanged. Hours where a forecast is missing
    keep the observed value; their number is returned so it can be reported.
    """
    in_window = (pd.to_datetime(data["local_date"]) >= pd.Timestamp(start)).to_numpy()
    ts = pd.to_datetime(data["timestamp_utc"], utc=True)
    out, n_missing = {}, {}
    for name, fc in variants.items():
        values = fc.reindex(ts).to_numpy()
        use = in_window & ~np.isnan(values)
        n_missing[name] = int((in_window & np.isnan(values)).sum())
        d = data.copy()
        d.loc[use, "temperature_c"] = values[use]
        out[name] = features.build_features(d)
    return out, n_missing


def forecast_error_summary(forecast: pd.Series, observed: pd.Series) -> dict:
    e = (forecast - observed.reindex(forecast.index)).dropna()
    return {"n_hours": int(len(e)), "bias_c": round(float(e.mean()), 2),
            "mae_c": round(float(e.abs().mean()), 2), "sd_c": round(float(e.std()), 2)}


# ---------------------------------------------------------------- D21: calibration

MIN_HISTORY_DAYS = 28
WINDOW_DAYS = 90
TARGET = 0.8


def mark_predicted_peak(pred: pd.DataFrame) -> pd.DataFrame:
    """Flag the hour with the highest *forecast* each day (known at issue time) and the
    hour with the highest *actual* load (known afterwards, used only for evaluation)."""
    p = pred.copy()
    p["is_pred_peak"] = p["forecast"] == p.groupby("local_date")["forecast"].transform("max")
    p["is_actual_peak"] = p["actual"] == p.groupby("local_date")["actual"].transform("max")
    # ties: keep the first hour only, so there is exactly one peak per day
    for col in ("is_pred_peak", "is_actual_peak"):
        first = p[p[col]].groupby("local_date")["local_hour"].transform("min")
        p.loc[p[col], col] = p.loc[p[col], "local_hour"].to_numpy() == first.to_numpy()
    return p


def online_calibration(pred: pd.DataFrame, min_days: int = MIN_HISTORY_DAYS,
                       window_days: int = WINDOW_DAYS, target: float = TARGET) -> pd.DataFrame:
    """Conformalised quantile intervals and a peak-hour margin, learned online.

    For each day D, the history is days D-window_days-1 .. D-2 (load known at issue).
    - Interval: score = max(q10 - y, y - q90) per hour; the adjustment is the
      `target` empirical quantile of past scores, computed separately for the predicted
      peak hour and the other hours; interval = [q10 - adj, q90 + adj] (CQR).
    - Peak margin: the median past residual (actual - forecast) at the predicted peak
      hour is added to that hour's point forecast.
    - Peak level: the median past gap between the day's highest actual load and the
      day's highest forecast is added to the forecast daily maximum (`daily_max_cal`).
      This is the quantity a balance-responsible party plans capacity against.
    Days with fewer than `min_days` days of history are returned with NaNs.
    """
    p = mark_predicted_peak(pred).sort_values(["local_date", "local_hour"]).reset_index(drop=True)
    p["score"] = np.maximum(p["q10"] - p["actual"], p["actual"] - p["q90"])
    p["resid"] = p["actual"] - p["forecast"]
    days = pd.to_datetime(p["local_date"])
    uniq = np.sort(days.unique())
    for c in ("adj", "margin", "q10_cal", "q90_cal", "forecast_cal", "level_margin"):
        p[c] = np.nan
    daily = p.groupby("local_date").agg(max_actual=("actual", "max"),
                                        max_forecast=("forecast", "max"))
    daily["gap"] = daily["max_actual"] - daily["max_forecast"]
    daily.index = pd.to_datetime(daily.index)
    for day in uniq:
        day = pd.Timestamp(day)
        hist_mask = (days <= day - pd.Timedelta(days=KNOWN_LAG_DAYS)) & (
            days > day - pd.Timedelta(days=KNOWN_LAG_DAYS + window_days))
        if days[hist_mask].nunique() < min_days:
            continue
        h = p[hist_mask]
        today = days == day
        for is_peak in (True, False):
            sel = today & (p["is_pred_peak"] == is_peak)
            scores = h.loc[h["is_pred_peak"] == is_peak, "score"].to_numpy()
            n = len(scores)
            level = min(1.0, np.ceil((n + 1) * target) / n)
            p.loc[sel, "adj"] = float(np.quantile(scores, level, method="higher"))
        margin = float(h.loc[h["is_pred_peak"], "resid"].median())
        p.loc[today & p["is_pred_peak"], "margin"] = margin
        p.loc[today & ~p["is_pred_peak"], "margin"] = 0.0
        known = daily[(daily.index <= day - pd.Timedelta(days=KNOWN_LAG_DAYS))
                      & (daily.index > day - pd.Timedelta(days=KNOWN_LAG_DAYS + window_days))]
        p.loc[today, "level_margin"] = float(known["gap"].median())
    p["q10_cal"] = p["q10"] - p["adj"]
    p["q90_cal"] = p["q90"] + p["adj"]
    p["forecast_cal"] = p["forecast"] + p["margin"]
    return p


def calibration_summary(cal: pd.DataFrame) -> dict:
    """Before/after on the days that have a calibration (same days for both)."""
    c = cal.dropna(subset=["adj"])
    out = {"n_days": int(c["local_date"].nunique()),
           "first_day": str(c["local_date"].min()), "last_day": str(c["local_date"].max())}
    segs = {"all hours": c, "actual daily peak hour": c[c["is_actual_peak"]],
            "predicted daily peak hour": c[c["is_pred_peak"]]}
    for name, g in segs.items():
        n_days = int(g["local_date"].nunique())
        row = {}
        for label, lo, hi in (("before", "q10", "q90"), ("after", "q10_cal", "q90_cal")):
            cov = metrics.interval_coverage(g["actual"], g[lo], g[hi])
            wl, wh = metrics.wilson_interval(int(round(cov * n_days)), n_days)
            row[label] = {"coverage_80_pct": round(100 * cov, 1),
                          "wilson95_on_days_pct": [round(100 * wl, 1), round(100 * wh, 1)],
                          "mean_width_mw": round(float((g[hi] - g[lo]).mean()), 1)}
        out[name] = row
    for name in ("actual daily peak hour", "predicted daily peak hour"):
        g = segs[name]
        out[name]["point"] = {
            "before": {"mae_mw": round(metrics.mae(g["actual"], g["forecast"]), 1),
                       "bias_mw": round(float((g["forecast"] - g["actual"]).mean()), 1)},
            "after": {"mae_mw": round(metrics.mae(g["actual"], g["forecast_cal"]), 1),
                      "bias_mw": round(float((g["forecast_cal"] - g["actual"]).mean()), 1)}}
    out["all hours"]["point_mae_mw"] = {
        "before": round(metrics.mae(c["actual"], c["forecast"]), 1),
        "after": round(metrics.mae(c["actual"], c["forecast_cal"]), 1)}
    daily = c.groupby("local_date").agg(max_actual=("actual", "max"),
                                        max_forecast=("forecast", "max"),
                                        level_margin=("level_margin", "first"))
    daily["max_forecast_cal"] = daily["max_forecast"] + daily["level_margin"]
    out["daily_maximum"] = {
        label: {"mae_mw": round(metrics.mae(daily["max_actual"], daily[col]), 1),
                "bias_mw": round(float((daily[col] - daily["max_actual"]).mean()), 1),
                "share_days_under_pct": round(100 * float(
                    (daily[col] < daily["max_actual"]).mean()), 1)}
        for label, col in (("before", "max_forecast"), ("after", "max_forecast_cal"))}
    out["daily_maximum"]["median_level_margin_mw"] = round(float(
        daily["level_margin"].median()), 1)
    out["peak_hour_hit_rate_pct"] = round(100 * float(
        c.loc[c["is_pred_peak"], "is_actual_peak"].mean()), 1)
    out["median_margin_mw"] = round(float(c.loc[c["is_pred_peak"], "margin"].median()), 1)
    return out
