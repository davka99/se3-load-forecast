"""Forecast accuracy metrics used throughout the backtest."""

from __future__ import annotations

import math

import numpy as np


def _arrays(y_true, y_pred) -> tuple[np.ndarray, np.ndarray]:
    a, b = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    if a.shape != b.shape:
        raise ValueError(f"Shape mismatch: {a.shape} vs {b.shape}")
    return a, b


def mae(y_true, y_pred) -> float:
    a, b = _arrays(y_true, y_pred)
    return float(np.mean(np.abs(a - b)))


def rmse(y_true, y_pred) -> float:
    a, b = _arrays(y_true, y_pred)
    return float(np.sqrt(np.mean((a - b) ** 2)))


def mape(y_true, y_pred) -> float:
    """Mean absolute percentage error in percent. Undefined if any actual is zero."""
    a, b = _arrays(y_true, y_pred)
    if np.any(a == 0):
        raise ValueError("MAPE is undefined when an actual value is zero")
    return float(100 * np.mean(np.abs((a - b) / a)))


def seasonal_naive_scale(y_train, season: int) -> float:
    """In-sample MAE of the seasonal-naive forecast, the denominator of MASE."""
    y = np.asarray(y_train, dtype=float)
    if len(y) <= season:
        raise ValueError("Training series must be longer than one season")
    return float(np.mean(np.abs(y[season:] - y[:-season])))


def mase(y_true, y_pred, y_train, season: int) -> float:
    """Mean absolute scaled error (Hyndman & Koehler 2006). <1 beats in-sample seasonal naive."""
    scale = seasonal_naive_scale(y_train, season)
    if scale == 0:
        raise ValueError("Seasonal-naive scale is zero")
    return mae(y_true, y_pred) / scale


def interval_coverage(y_true, lower, upper) -> float:
    a = np.asarray(y_true, dtype=float)
    lo, hi = np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)
    if np.any(lo > hi):
        raise ValueError("lower bound above upper bound")
    return float(np.mean((a >= lo) & (a <= hi)))


def pinball_loss(y_true, y_quantile, q: float) -> float:
    if not 0 < q < 1:
        raise ValueError("q must be in (0, 1)")
    a, b = _arrays(y_true, y_quantile)
    diff = a - b
    return float(np.mean(np.maximum(q * diff, (q - 1) * diff)))


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion (e.g. empirical interval coverage).

    Note: hourly forecast errors are autocorrelated, so this interval is too
    narrow for hourly coverage; use it on daily or block-aggregated counts.
    """
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError("need n > 0 and 0 <= successes <= n")
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return centre - half, centre + half


def block_bootstrap_mae_diff(y_true, pred_a, pred_b, blocks, n_boot: int = 2000,
                             seed: int = 0, level: float = 0.95) -> dict:
    """Paired block bootstrap of MAE(a) - MAE(b).

    Whole blocks (e.g. weeks) are resampled with replacement so that within-block
    autocorrelation of forecast errors is preserved. Negative = a is better.
    """
    y, a, b = (np.asarray(v, dtype=float) for v in (y_true, pred_a, pred_b))
    blocks = np.asarray(blocks)
    diff = np.abs(y - a) - np.abs(y - b)
    uniq, inv = np.unique(blocks, return_inverse=True)
    sums = np.bincount(inv, weights=diff)
    counts = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
    boot = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    alpha = (1 - level) / 2
    return {"diff": float(diff.mean()),
            "ci": [float(np.quantile(boot, alpha)), float(np.quantile(boot, 1 - alpha))],
            "n_blocks": int(len(uniq))}
