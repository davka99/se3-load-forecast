"""M8 evaluation helpers: segments, interval statistics and the weather-noise simulation.

Everything here follows the pre-registration in docs/decisions.md D19.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import features, metrics

COLD_C = -5.0


def add_context(pred: pd.DataFrame, feat: pd.DataFrame, data: pd.DataFrame) -> pd.DataFrame:
    """Attach the columns segments need (temperature, day type, source flag, peak hour)."""
    ctx = feat[["timestamp_utc", "temp", "is_public_holiday", "is_de_facto_holiday",
                "is_weekend", "daytype"]]
    ctx = ctx.merge(data[["timestamp_utc", "temp_from_latest"]], on="timestamp_utc",
                    validate="one_to_one")
    out = pred.merge(ctx, on="timestamp_utc", how="left", validate="many_to_one")
    daily_max = out.groupby(["model", "local_date"])["actual"].transform("max")
    out["is_daily_peak"] = out["actual"] == daily_max
    m = pd.to_datetime(out["local_date"]).dt.month
    out["season"] = np.where(m.isin([11, 12, 1, 2, 3]), "winter (Nov-Mar)", "summer (Apr-Oct)")
    return out


SEGMENTS = {
    "all": lambda p: pd.Series(True, index=p.index),
    "winter (Nov-Mar)": lambda p: p["season"] == "winter (Nov-Mar)",
    "summer (Apr-Oct)": lambda p: p["season"] == "summer (Apr-Oct)",
    f"cold hours (< {COLD_C:.0f} °C)": lambda p: p["temp"] < COLD_C,
    "daily peak hour": lambda p: p["is_daily_peak"],
    "holidays and eves": lambda p: p["is_public_holiday"] | p["is_de_facto_holiday"],
    "weekdays": lambda p: ~p["is_weekend"] & ~p["is_public_holiday"] & ~p["is_de_facto_holiday"],
    "weekends": lambda p: p["is_weekend"],
    "Aug-Oct 2026 (SMHI latest-months temp)": lambda p: p["temp_from_latest"].astype(bool),
}


def segment_table(pred: pd.DataFrame) -> list[dict]:
    rows = []
    for seg, rule in SEGMENTS.items():
        for model, g in pred.groupby("model"):
            g = g[rule(g)]
            if g.empty:
                continue
            err = g["forecast"] - g["actual"]
            row = {"segment": seg, "model": model, "n_hours": int(len(g)),
                   "n_days": int(g["local_date"].nunique()),
                   "mae_mw": round(float(err.abs().mean()), 1),
                   "mape_pct": round(float(100 * (err.abs() / g["actual"]).mean()), 2),
                   "bias_mw": round(float(err.mean()), 1)}
            if g["q10"].notna().all():
                row["coverage_80_pct"] = round(100 * metrics.interval_coverage(
                    g["actual"], g["q10"], g["q90"]), 1)
            rows.append(row)
    return rows


def coverage_with_day_ci(pred: pd.DataFrame) -> dict:
    """Hourly coverage with a Wilson CI using the number of days as sample size."""
    cov = metrics.interval_coverage(pred["actual"], pred["q10"], pred["q90"])
    n_days = int(pred["local_date"].nunique())
    lo, hi = metrics.wilson_interval(int(round(cov * n_days)), n_days)
    return {"coverage_80_pct": round(100 * cov, 1), "n_days": n_days,
            "wilson95_on_days_pct": [round(100 * lo, 1), round(100 * hi, 1)],
            "pinball_q10": round(metrics.pinball_loss(pred["actual"], pred["q10"], 0.1), 1),
            "pinball_q90": round(metrics.pinball_loss(pred["actual"], pred["q90"], 0.9), 1),
            "mean_width_mw": round(float((pred["q90"] - pred["q10"]).mean()), 1)}


def daily_abs_error(pred: pd.DataFrame) -> pd.Series:
    return (pred["forecast"] - pred["actual"]).abs().groupby(pred["local_date"]).mean()


def noisy_feature_sets(data: pd.DataFrame, start: str, n_reps: int, sd: float, rho: float,
                       seed: int) -> dict[str, pd.DataFrame]:
    """Feature frames where the temperature from `start` onwards carries simulated
    day-ahead forecast error (AR(1) across hours). History before `start` and all load
    values are unchanged, so only the weather inputs for the forecast days differ.

    Simplification (stated in D19/R3): one continuous error path per replicate rather
    than a fresh forecast per issue time, and the same path also perturbs the smoothed
    temperature features.
    """
    rng = np.random.default_rng(seed)
    in_window = (pd.to_datetime(data["local_date"]) >= pd.Timestamp(start)).to_numpy()
    out = {}
    for r in range(n_reps):
        noise = np.zeros(len(data))
        noise[in_window] = metrics.ar1_noise(int(in_window.sum()), sd, rho, rng)
        noisy = data.copy()
        noisy["temperature_c"] = noisy["temperature_c"] + noise
        out[f"temp_noise_{r + 1}"] = features.build_features(noisy)
    return out
