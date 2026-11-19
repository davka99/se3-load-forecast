"""D11: choose how to handle the post-2022 level shift, on the validation year only.

Selection rule (fixed in docs/decisions.md D11 before any model results):
the option with the lowest validation MAE for the linear model *with* load lags,
validation = 2024-11-01 .. 2025-10-31 (local dates), training ends 2024-10-31.
The backtest year (from 2025-11-01) is never touched here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from . import features, metrics

TRAIN_START = "2019-01-08"   # first local date with a complete D-7 lag
TRAIN_END = "2024-10-31"
VAL_START, VAL_END = "2024-11-01", "2025-10-31"
POST_START = "2023-01-01"
HALF_LIFE_DAYS = 365

OPTIONS = {
    "all_years_no_adjustment": {"start": TRAIN_START, "regime": False, "weighted": False},
    "all_years_regime_terms": {"start": TRAIN_START, "regime": True, "weighted": False},
    "all_years_recency_weighted": {"start": TRAIN_START, "regime": False, "weighted": True},
    "post_2022_only": {"start": POST_START, "regime": False, "weighted": False},
}


def _fit_predict(feat: pd.DataFrame, opt: dict, use_lags: bool) -> tuple[np.ndarray, np.ndarray]:
    X = features.linear_design(feat, regime_terms=opt["regime"], use_lags=use_lags)
    d = pd.to_datetime(feat["local_date"])
    train = (d >= opt["start"]) & (d <= TRAIN_END)
    val = (d >= VAL_START) & (d <= VAL_END)
    ok = X.notna().all(axis=1) & feat["load_mw"].notna()
    tr, va = train & ok, val & ok
    weights = None
    if opt["weighted"]:
        age = (pd.Timestamp(TRAIN_END) - d[tr]).dt.days.to_numpy()
        weights = 0.5 ** (age / HALF_LIFE_DAYS)
    model = Ridge(alpha=1.0).fit(X[tr], feat.loc[tr, "load_mw"], sample_weight=weights)
    return feat.loc[va, "load_mw"].to_numpy(), model.predict(X[va])


def evaluate(feat: pd.DataFrame, seed: int = 0) -> tuple[list[dict], list[dict]]:
    """Validation metrics per option, plus paired weekly-block bootstrap comparisons
    of the chosen option against every other option (lags model)."""
    d = pd.to_datetime(feat["local_date"])
    rows, preds = [], {}
    for use_lags in (True, False):
        for name, opt in OPTIONS.items():
            y, f = _fit_predict(feat, opt, use_lags)
            preds[(name, use_lags)] = (y, f)
            rows.append({
                "option": name, "use_lags": use_lags, "n_val_hours": int(len(y)),
                "mae_mw": round(metrics.mae(y, f), 1),
                "mape_pct": round(metrics.mape(y, f), 2),
                "bias_mw": round(float(np.mean(f - y)), 1),
            })
    best = choose(rows)
    val = (d >= VAL_START) & (d <= VAL_END)
    X_ok = features.linear_design(feat, use_lags=True).notna().all(axis=1)
    weeks = d[val & X_ok].dt.strftime("%G-%V").to_numpy()
    y, f_best = preds[(best, True)]
    comparisons = []
    for name in OPTIONS:
        if name == best:
            continue
        _, f_other = preds[(name, True)]
        res = metrics.block_bootstrap_mae_diff(y, f_best, f_other, weeks, seed=seed)
        comparisons.append({"best": best, "vs": name, "mae_diff_mw": round(res["diff"], 1),
                            "ci95_mw": [round(c, 1) for c in res["ci"]],
                            "n_week_blocks": res["n_blocks"]})
    return rows, comparisons


def choose(rows: list[dict]) -> str:
    primary = [r for r in rows if r["use_lags"]]
    return min(primary, key=lambda r: r["mae_mw"])["option"]
