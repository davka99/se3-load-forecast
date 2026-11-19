import numpy as np
import pandas as pd
import pytest

from se3load import backtest, features, quality


def make_feat(start="2024-01-01", end="2024-04-01", seed=0):
    ts = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"),
                       freq="1h", inclusive="left")
    df = quality.add_local_time(pd.DataFrame({"timestamp_utc": ts}))
    rng = np.random.default_rng(seed)
    df["load_mw"] = 9000 + 1000 * np.sin(2 * np.pi * df["local_hour"] / 24) \
        + rng.normal(0, 50, len(df))
    df["temperature_c"] = 0.0
    return features.build_features(df)


class SpyModel:
    """Predicts the mean of its training targets and remembers what it was fitted on."""
    name = "spy"
    needs_fit = True

    def __init__(self):
        self.max_dates = []

    def fit(self, feat, y, weights):
        self.max_dates.append(pd.to_datetime(feat["local_date"]).max())
        self.mean = float(np.average(y, weights=weights))

    def predict(self, feat):
        return pd.DataFrame({"forecast": self.mean, "q10": self.mean - 100,
                             "q90": self.mean + 100}, index=feat.index)


def test_baseline_forecasts_equal_lag_columns():
    feat = make_feat()
    res = backtest.run(feat, backtest.LagBaseline("load_d7_same_hour", "snaive"),
                       "2024-02-01", "2024-02-10")
    merged = res.predictions.merge(feat[["timestamp_utc", "load_d7_same_hour"]],
                                   on="timestamp_utc")
    assert np.allclose(merged["forecast"], merged["load_d7_same_hour"])
    assert res.fits == []
    assert len(res.predictions) == 10 * 24
    first = res.predictions.iloc[0]
    assert first["issued_local"] == "2024-01-31 12:00"


def test_fits_use_only_data_up_to_d_minus_2_and_refit_schedule():
    feat = make_feat()
    spy = SpyModel()
    res = backtest.run(feat, spy, "2024-02-01", "2024-03-31", refit_days=28)
    assert len(res.fits) == 3  # Feb 1, Feb 29, Mar 28
    for f, seen in zip(res.fits, spy.max_dates):
        first = pd.Timestamp(f["first_target"])
        assert seen <= first - pd.Timedelta(days=2)
        assert seen == pd.Timestamp(f["train_max_date"])
    backtest.assert_no_leakage(res)


def test_assert_no_leakage_catches_bad_fit():
    bad = backtest.BacktestResult(pd.DataFrame(), [{"first_target": "2024-02-10",
                                                    "train_max_date": "2024-02-09"}])
    with pytest.raises(AssertionError):
        backtest.assert_no_leakage(bad)


def test_recency_weights_half_life():
    d = pd.Series(["2024-01-01", "2023-01-01"])
    w = backtest.recency_weights(d, pd.Timestamp("2024-01-01"), 365)
    assert w[0] == pytest.approx(1.0)
    assert w[1] == pytest.approx(0.5, rel=1e-3)
    assert backtest.recency_weights(d, pd.Timestamp("2024-01-01"), None) is None


def test_summarise_with_intervals_and_mase():
    pred = pd.DataFrame({"actual": [100.0, 200.0, 300.0, 400.0],
                         "forecast": [110.0, 190.0, 300.0, np.nan],
                         "q10": [90.0, 195.0, 250.0, 0.0], "q90": [120.0, 205.0, 350.0, 1.0]})
    s = backtest.summarise(pred, mase_scale=10.0)
    assert s["n_hours"] == 3 and s["n_missing_forecast"] == 1
    assert s["mae_mw"] == pytest.approx(20 / 3, abs=0.05)
    assert s["mase"] == pytest.approx(2 / 3, abs=0.001)
    assert s["coverage_80_pct"] == pytest.approx(100.0)
    assert s["bias_mw"] == pytest.approx(0.0)


def test_mase_scale_from_history_uses_only_earlier_dates():
    feat = make_feat()
    data = feat[["local_date", "load_mw"]].copy()
    s1 = backtest.mase_scale_from_history(data, "2024-03-01")
    data.loc[pd.to_datetime(data["local_date"]) >= "2024-03-01", "load_mw"] = 1e9
    assert backtest.mase_scale_from_history(data, "2024-03-01") == pytest.approx(s1)
