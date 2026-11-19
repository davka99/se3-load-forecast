"""Rolling-origin backtest that mimics the day-ahead bid.

For every target local day D in the evaluation window:
- the forecast is issued at 12:00 local on D-1;
- a model may only be fitted on rows with local_date <= D-2 (D12);
- features for D come from `features.build_features`, which only reads load from
  D-2 and D-7 and the (perfect-forecast) temperature of D (D4).

Models are refitted every `refit_days` days; between refits the latest fit is reused,
which is how a production forecast is usually run. Every fit records its cutoff so the
no-leakage rule can be asserted after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import pandas as pd

from . import metrics


class Model(Protocol):
    name: str
    needs_fit: bool

    def fit(self, feat: pd.DataFrame, y: pd.Series, weights: np.ndarray | None) -> None: ...

    def predict(self, feat: pd.DataFrame) -> pd.DataFrame:
        """Return a frame with column 'forecast' and optionally 'q10', 'q90'."""
        ...


# ---------------------------------------------------------------- baselines

@dataclass
class LagBaseline:
    """Forecast = a load lag column. No fitting.

    'seasonal_naive_d7': same local hour one week earlier (the classic seasonal naive).
    'naive_d2': same local hour on D-2, the most recent day known at bid time.
    """
    column: str
    name: str
    needs_fit: bool = False

    def fit(self, feat, y, weights) -> None:  # pragma: no cover - nothing to fit
        return None

    def predict(self, feat: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"forecast": feat[self.column].to_numpy()}, index=feat.index)


def baselines() -> list[LagBaseline]:
    return [LagBaseline("load_d7_same_hour", "seasonal_naive_d7"),
            LagBaseline("load_d2_same_hour", "naive_d2")]


# ---------------------------------------------------------------- framework

def recency_weights(dates: pd.Series, cutoff: pd.Timestamp, half_life_days: float | None):
    if half_life_days is None:
        return None
    age = (cutoff - pd.to_datetime(dates)).dt.days.to_numpy()
    return 0.5 ** (age / half_life_days)


@dataclass
class BacktestResult:
    predictions: pd.DataFrame
    fits: list[dict] = field(default_factory=list)


def run(feat: pd.DataFrame, model: Model, start: str, end: str, refit_days: int = 28,
        train_start: str = "2019-01-08", half_life_days: float | None = 365) -> BacktestResult:
    """Rolling-origin backtest over target local dates start..end (inclusive)."""
    dates = pd.to_datetime(feat["local_date"])
    targets = pd.date_range(start, end, freq="D")
    preds, fits = [], []
    last_fit: pd.Timestamp | None = None
    for day in targets:
        cutoff = day - pd.Timedelta(days=2)  # last local date whose load is known
        if model.needs_fit and (last_fit is None or (day - last_fit).days >= refit_days):
            train = (dates >= train_start) & (dates <= cutoff)
            cols_ok = feat.loc[train].notna().all(axis=1) & feat.loc[train, "load_mw"].notna()
            tr = feat.loc[train][cols_ok]
            w = recency_weights(tr["local_date"], cutoff, half_life_days)
            model.fit(tr, tr["load_mw"], w)
            last_fit = day
            fits.append({"model": model.name, "first_target": str(day.date()),
                         "train_cutoff": str(cutoff.date()),
                         "train_max_date": str(pd.to_datetime(tr["local_date"]).max().date()),
                         "n_train": int(len(tr))})
        rows = feat[dates == day]
        if rows.empty:
            continue
        p = model.predict(rows)
        out = rows[["timestamp_utc", "local_date", "local_hour", "load_mw"]].copy()
        out = out.rename(columns={"load_mw": "actual"})
        out["model"] = model.name
        out["issued_local"] = f"{(day - pd.Timedelta(days=1)).date()} 12:00"
        for c in p.columns:
            out[c] = p[c].to_numpy()
        preds.append(out)
    return BacktestResult(pd.concat(preds, ignore_index=True), fits)


def assert_no_leakage(result: BacktestResult) -> None:
    """Every fit used only rows dated at least two days before its first target."""
    for f in result.fits:
        first = pd.Timestamp(f["first_target"])
        if pd.Timestamp(f["train_max_date"]) > first - pd.Timedelta(days=2):
            raise AssertionError(f"Leakage in fit {f}")


def summarise(pred: pd.DataFrame, mase_scale: float) -> dict:
    """Accuracy of one model's predictions (rows with a forecast)."""
    p = pred.dropna(subset=["forecast"])
    y, f = p["actual"].to_numpy(), p["forecast"].to_numpy()
    out = {"n_hours": int(len(p)),
           "n_missing_forecast": int(pred["forecast"].isna().sum()),
           "mae_mw": round(metrics.mae(y, f), 1),
           "rmse_mw": round(metrics.rmse(y, f), 1),
           "mape_pct": round(metrics.mape(y, f), 2),
           "mase": round(metrics.mae(y, f) / mase_scale, 3),
           "bias_mw": round(float(np.mean(f - y)), 1)}
    if {"q10", "q90"} <= set(p.columns):
        out["coverage_80_pct"] = round(100 * metrics.interval_coverage(y, p["q10"], p["q90"]), 1)
    return out


def mase_scale_from_history(data: pd.DataFrame, before: str, season: int = 168) -> float:
    """In-sample seasonal-naive MAE on the hourly history before the evaluation window."""
    hist = data[pd.to_datetime(data["local_date"]) < pd.Timestamp(before)]
    return metrics.seasonal_naive_scale(hist["load_mw"].to_numpy(), season)
