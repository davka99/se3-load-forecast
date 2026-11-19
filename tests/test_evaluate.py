import numpy as np
import pandas as pd
import pytest

from se3load import evaluate, metrics


def test_diebold_mariano_detects_difference_and_not_noise():
    rng = np.random.default_rng(0)
    base = rng.gamma(5, 40, 365)
    better = base * 0.8
    r = metrics.diebold_mariano(better, base)
    assert r["statistic"] < -3 and r["p_value"] < 0.01
    noise_a, noise_b = rng.gamma(5, 40, 365), rng.gamma(5, 40, 365)
    assert metrics.diebold_mariano(noise_a, noise_b)["p_value"] > 0.01
    with pytest.raises(ValueError):
        metrics.diebold_mariano([1.0] * 5, [2.0] * 5)


def test_ar1_noise_moments():
    x = metrics.ar1_noise(200_000, 1.5, 0.9, np.random.default_rng(1))
    assert np.std(x) == pytest.approx(1.5, rel=0.05)
    assert np.corrcoef(x[1:], x[:-1])[0, 1] == pytest.approx(0.9, abs=0.01)


def _pred(n_days=10, model="m"):
    ts = pd.date_range("2026-01-01T00:00Z", periods=24 * n_days, freq="1h")
    p = pd.DataFrame({"timestamp_utc": ts,
                      "local_date": ts.tz_convert("Europe/Stockholm").strftime("%Y-%m-%d"),
                      "model": model, "actual": 100.0, "forecast": 110.0,
                      "q10": 90.0, "q90": 120.0})
    return p


def test_coverage_with_day_ci_uses_days_as_sample_size():
    p = _pred(30)
    p.loc[p.index[:72], "actual"] = 200.0  # 3 days fully outside the interval
    r = evaluate.coverage_with_day_ci(p)
    assert r["coverage_80_pct"] == pytest.approx(90.0, abs=0.5)
    n_days = p["local_date"].nunique()  # 31: UTC days straddle local midnight
    assert r["n_days"] == n_days
    lo, hi = metrics.wilson_interval(round(0.9 * n_days), n_days)
    assert r["wilson95_on_days_pct"] == [round(100 * lo, 1), round(100 * hi, 1)]


def test_segment_table_and_peak_hour():
    p = _pred(3)
    p["actual"] = 100.0 + (p["timestamp_utc"].dt.hour == 16) * 50
    p["temp"] = -10.0
    p["is_public_holiday"] = False
    p["is_de_facto_holiday"] = False
    p["is_weekend"] = False
    p["temp_from_latest"] = False
    daily_max = p.groupby(["model", "local_date"])["actual"].transform("max")
    p["is_daily_peak"] = p["actual"] == daily_max
    p["season"] = "winter (Nov-Mar)"
    rows = {r["segment"]: r for r in evaluate.segment_table(p)}
    assert rows["all"]["n_hours"] == 72
    assert rows["cold hours (< -5 °C)"]["n_hours"] == 72
    assert "summer (Apr-Oct)" not in rows
    assert rows["daily peak hour"]["n_hours"] >= 3
    assert rows["all"]["coverage_80_pct"] < 100
