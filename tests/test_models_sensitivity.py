import numpy as np
import pandas as pd
import pytest

from se3load import backtest, build_dataset, features, models, quality, sensitivity


def synthetic(start="2023-01-01", end="2024-07-01", slope=300.0, noise=80.0, seed=0):
    """Load = profile + slope * HDD(daily-ish temperature) + noise. Temperature varies
    within months so the slope is identified separately from the season."""
    ts = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"),
                       freq="1h", inclusive="left")
    df = quality.add_local_time(pd.DataFrame({"timestamp_utc": ts}))
    rng = np.random.default_rng(seed)
    days = pd.to_datetime(df["local_date"])
    day_codes, day_idx = np.unique(days, return_inverse=True)
    season = 8 - 10 * np.cos(2 * np.pi * days.dt.dayofyear.to_numpy() / 365.25)
    weather = rng.normal(0, 4, len(day_codes))[day_idx]
    df["temperature_c"] = season + weather
    hdd = np.clip(features.HDD_BASE_C - df["temperature_c"], 0, None)
    df["load_mw"] = 8000 + 800 * np.sin(2 * np.pi * (df["local_hour"] - 6) / 24) \
        - 600 * (days.dt.dayofweek >= 5) + slope * hdd + rng.normal(0, noise, len(df))
    return df


def test_daily_sensitivity_recovers_known_slope():
    pytest.importorskip("statsmodels")
    feat = features.build_features(synthetic(slope=300.0))
    res = sensitivity.daily_sensitivity(feat, "2023-01-08", "2024-06-30")
    assert res["ci95"][0] < 300 < res["ci95"][1]
    assert abs(res["mw_per_degc_below_15"] - 300) < 20
    assert res["hac_se"] > 0


def test_hourly_slopes_recover_known_slope():
    pytest.importorskip("statsmodels")
    feat = features.build_features(synthetic(slope=250.0, end="2023-12-31"))
    slopes = sensitivity.hourly_slopes(feat, "2023-01-08", "2023-12-30")
    assert len(slopes) == 24
    assert all(abs(v["slope"] - 250) < 40 for v in slopes.values())


def test_linear_model_beats_seasonal_naive_and_intervals_are_calibrated():
    feat = features.build_features(synthetic())
    lin = backtest.run(feat, models.LinearModel(), "2024-04-01", "2024-06-30",
                       half_life_days=None)
    backtest.assert_no_leakage(lin)
    snv = backtest.run(feat, backtest.LagBaseline("load_d7_same_hour", "snaive"),
                       "2024-04-01", "2024-06-30")
    s_lin = backtest.summarise(lin.predictions, 1.0)
    s_sn = backtest.summarise(snv.predictions, 1.0)
    assert s_lin["mae_mw"] < 0.5 * s_sn["mae_mw"]
    assert 70 <= s_lin["coverage_80_pct"] <= 90  # split-conformal on held-out residuals
    p = lin.predictions
    assert (p["q10"] <= p["forecast"]).all() and (p["forecast"] <= p["q90"]).all()


def test_linear_model_handles_unseen_columns_at_predict_time():
    feat = features.build_features(synthetic(end="2023-06-01"))
    m = models.LinearModel(calib_days=30)
    train = feat.dropna()
    train = train[train["daytype"] != "bridge"]  # bridge days unseen in training
    m.fit(train, train["load_mw"], None)
    out = m.predict(feat.dropna())
    assert out["forecast"].notna().all()


def test_with_alternative_temperature_keeps_rows():
    data = synthetic(end="2023-01-10")
    data["timestamp_utc"] = pd.to_datetime(data["timestamp_utc"], utc=True)
    raw = pd.DataFrame({"timestamp_utc": data["timestamp_utc"].astype(str), "station": "S",
                        "temperature_c": -3.0, "quality": "G", "source": "archive"})
    out = build_dataset.with_alternative_temperature(data, raw, {"S": 1.0})
    assert len(out) == len(data)
    assert (out["temperature_c"] == -3.0).all()
    assert out["load_mw"].equals(data["load_mw"])
