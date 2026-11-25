import numpy as np
import pandas as pd
import pytest

from se3load import extensions, openmeteo


def _payload(n_hours=3, base=1.0):
    times = pd.date_range("2025-11-01", periods=n_hours, freq="h").strftime("%Y-%m-%dT%H:%M")
    return {"hourly": {"time": list(times),
                       "temperature_2m_previous_day1": [base + i for i in range(n_hours)],
                       "temperature_2m_previous_day2": [base + 10 + i for i in range(n_hours)]}}


def test_params_join_coordinates_and_variables():
    q = openmeteo.params([59.34, 57.7], [18.05, 11.97], "2025-11-01", "2025-11-30")
    assert q["latitude"] == "59.3400,57.7000"
    assert q["hourly"] == "temperature_2m_previous_day1,temperature_2m_previous_day2"
    assert q["timezone"] == "GMT"
    with pytest.raises(ValueError):
        openmeteo.params([59.0], [], "2025-11-01", "2025-11-02")


def test_parse_single_and_multiple_locations():
    one = openmeteo.parse(_payload(), ["A"])
    assert len(one) == 6
    assert one["timestamp_utc"].iloc[0] == pd.Timestamp("2025-11-01T00:00Z")
    assert one.loc[one["lead_day"] == 2, "temperature_c"].tolist() == [11.0, 12.0, 13.0]
    two = openmeteo.parse([_payload(), _payload(base=5.0)], ["A", "B"])
    assert set(two["station"]) == {"A", "B"}
    with pytest.raises(ValueError):
        openmeteo.parse([_payload()], ["A", "B"])


def test_parse_raises_on_missing_variable_and_error():
    bad = _payload()
    del bad["hourly"]["temperature_2m_previous_day2"]
    with pytest.raises(KeyError):
        openmeteo.parse(bad, ["A"])
    with pytest.raises(ValueError):
        openmeteo.parse({"error": True, "reason": "x"}, ["A"])


def test_station_coordinates():
    js = {"station": [{"key": "98230", "latitude": 59.34, "longitude": 18.05}]}
    assert openmeteo.station_coordinates(js, [98230]) == {98230: (59.34, 18.05)}
    with pytest.raises(KeyError):
        openmeteo.station_coordinates(js, [1])


def test_bias_correction_uses_only_known_days():
    idx = pd.date_range("2025-09-01", periods=24 * 60, freq="h", tz="UTC")
    observed = pd.Series(0.0, index=idx)
    # forecast is 2 degC too warm before 2025-10-15 and exact afterwards
    fc = pd.Series(np.where(idx < pd.Timestamp("2025-10-15", tz="UTC"), 2.0, 0.0), index=idx)
    corr, ok = extensions.bias_corrected(fc, observed, window_days=7, known_lag_days=2)
    # warm-up: first 7 + 2 days are not corrected
    assert not ok[idx < pd.Timestamp("2025-09-09", tz="UTC")].any()
    assert ok[pd.Timestamp("2025-09-10 05:00", tz="UTC")]
    assert corr[pd.Timestamp("2025-09-20 05:00", tz="UTC")] == pytest.approx(0.0)
    # on 2025-10-16 the correction still reflects days up to 2025-10-14 (not yet the change)
    assert corr[pd.Timestamp("2025-10-16 05:00", tz="UTC")] == pytest.approx(-2.0)
    # after a full window of exact forecasts the correction is gone
    assert corr[pd.Timestamp("2025-10-25 05:00", tz="UTC")] == pytest.approx(0.0)


def _pred(n_days=60, under_peak=100.0, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for d in pd.date_range("2025-11-01", periods=n_days, freq="D"):
        for h in range(24):
            f = 1000 + 300 * np.sin(np.pi * h / 24)  # unique maximum at h = 12
            a = f + rng.normal(0, 50) + (under_peak if h == 12 else 0)
            rows.append({"local_date": d.strftime("%Y-%m-%d"), "local_hour": h,
                         "timestamp_utc": d + pd.Timedelta(hours=h), "actual": a,
                         "forecast": f, "q10": f - 20, "q90": f + 20})
    return pd.DataFrame(rows)


def test_predicted_peak_is_one_hour_per_day():
    p = extensions.mark_predicted_peak(_pred(5))
    assert p.groupby("local_date")["is_pred_peak"].sum().eq(1).all()
    assert p.groupby("local_date")["is_actual_peak"].sum().eq(1).all()


def test_online_calibration_fixes_narrow_intervals_and_peak_bias():
    p = _pred(90)
    cal = extensions.online_calibration(p, min_days=28, window_days=60)
    # first days have no calibration (need 28 known days, known up to D-2)
    first = pd.Timestamp("2025-11-01")
    assert cal.loc[pd.to_datetime(cal["local_date"]) < first + pd.Timedelta(days=29),
                   "adj"].isna().all()
    s = extensions.calibration_summary(cal)
    assert s["all hours"]["before"]["coverage_80_pct"] < 40
    assert 74 <= s["all hours"]["after"]["coverage_80_pct"] <= 86
    pk = s["predicted daily peak hour"]["point"]
    assert pk["before"]["bias_mw"] == pytest.approx(-100, abs=15)
    assert abs(pk["after"]["bias_mw"]) < 25


def test_online_calibration_does_not_look_ahead():
    p = _pred(60)
    base = extensions.online_calibration(p, min_days=28, window_days=60)
    changed = p.copy()
    last = changed["local_date"] == changed["local_date"].max()
    changed.loc[last, "actual"] += 10_000  # a shock on the last day only
    after = extensions.online_calibration(changed, min_days=28, window_days=60)
    keep = base["local_date"] < base["local_date"].max()
    pd.testing.assert_series_equal(base.loc[keep, "adj"], after.loc[keep, "adj"])


def test_daily_maximum_margin_reduces_under_forecast():
    s = extensions.calibration_summary(extensions.online_calibration(_pred(90), 28, 60))
    dm = s["daily_maximum"]
    assert dm["before"]["bias_mw"] < -50
    assert abs(dm["after"]["bias_mw"]) < abs(dm["before"]["bias_mw"])
