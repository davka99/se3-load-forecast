import numpy as np
import pandas as pd
import pytest

from se3load import quality


def utc_range(start, periods, freq="1h"):
    return pd.date_range(start, periods=periods, freq=freq, tz="UTC")


def test_detect_resolution_switch_and_expected_periods():
    ts = list(utc_range("2023-05-21T20:00", 2)) + list(utc_range("2023-05-21T22:00", 8, "15min"))
    raw = pd.DataFrame({"timestamp_utc": ts, "consumption": -1.0})
    switch = quality.detect_resolution_switch(raw)
    assert switch == pd.Timestamp("2023-05-21T22:00Z")
    hourly = quality.hourly_load(raw)
    exp = quality.expected_periods(hourly["timestamp_utc"], switch)
    assert list(exp) == [1, 1, 4, 4]
    assert list(hourly["n_periods"]) == list(exp)
    assert list(hourly["load_mw"]) == [1.0, 1.0, 4.0, 4.0]  # sign flipped, energy summed


def test_detect_resolution_switch_none_for_hourly():
    raw = pd.DataFrame({"timestamp_utc": utc_range("2020-01-01", 5), "consumption": -1.0})
    assert quality.detect_resolution_switch(raw) is None


def test_missing_hours():
    ts = pd.Series(utc_range("2024-01-01", 5).delete(2))
    gaps = quality.missing_hours(ts, "2024-01-01", "2024-01-01T05:00")
    assert list(gaps) == [pd.Timestamp("2024-01-01T02:00Z")]


def test_robust_z_flags_injected_spike_only():
    rng = np.random.default_rng(1)
    idx = utc_range("2024-01-01", 24 * 7 * 10)
    base = 9000 + 1500 * np.sin(2 * np.pi * np.arange(len(idx)) / 24)
    load = pd.Series(base + rng.normal(0, 50, len(idx)), index=idx)
    load.iloc[24 * 7 * 5 + 10] += 3000
    z = quality.robust_same_hour_z(load)
    flagged = z.abs() > quality.LOAD_ROBUST_Z
    assert flagged.sum() == 1
    assert flagged.iloc[24 * 7 * 5 + 10]


def test_stuck_runs():
    s = pd.Series([1.0, 2.0, 2.0, 2.0, 3.0, 3.0, np.nan, np.nan, np.nan])
    assert list(quality.stuck_runs(s, 3)) == [False, True, True, True, False, False,
                                              False, False, False]


def _long(values, station="A", start="2024-01-01"):
    return pd.DataFrame({"timestamp_utc": utc_range(start, len(values)), "station": station,
                         "temperature_c": values, "quality": "G", "source": "archive"})


def test_clean_station_temperature_rules():
    df = _long([1.0, 2.0, 60.0, 3.0, 4.0, 15.0, 4.5, 5.0])
    out, counts = quality.clean_station_temperature(df)
    assert counts == {"out_of_range": 1, "spike": 1}
    assert out["temperature_c"].isna().sum() == 2
    assert set(out.loc[out["temperature_c"].isna(), "temp_rule"]) == {"out_of_range", "spike"}


def test_spike_rule_ignores_real_rapid_change():
    # A steady 9 °C drop over 3 hours (front passage) is not an isolated spike.
    df = _long([10.0, 7.0, 4.0, 1.0, 1.0])
    _, counts = quality.clean_station_temperature(df)
    assert counts["spike"] == 0


def test_hourly_temperature_fills_short_gaps_only():
    vals = [0.0, np.nan, np.nan, 3.0] + [3.0] + [np.nan] * 5 + [9.0]
    df = _long(vals).dropna(subset=["temperature_c"])[["timestamp_utc", "station",
                                                      "temperature_c"]]
    out = quality.hourly_temperature(df, {"A": 1.0}, "2024-01-01", "2024-01-01T11:00")
    assert out["temperature_c"].iloc[1] == pytest.approx(1.0)
    assert out["temperature_c"].iloc[2] == pytest.approx(2.0)
    assert out["temp_imputed"].sum() == 2
    assert out["temperature_c"].iloc[5:10].isna().all()  # 5-hour gap stays missing
    assert (out.loc[out["temperature_c"].isna(), "weight_coverage"] == 0).all()


def test_local_time_dst_day_lengths():
    df = pd.DataFrame({"timestamp_utc": utc_range("2025-03-29T23:00", 24 * 3)})
    out = quality.add_local_time(df)
    lengths = quality.local_day_lengths(out)
    assert lengths["2025-03-30"] == 23
    df2 = pd.DataFrame({"timestamp_utc": utc_range("2025-10-25T22:00", 24 * 3)})
    assert quality.local_day_lengths(quality.add_local_time(df2))["2025-10-26"] == 25


def test_heating_degree_hours():
    assert quality.heating_degree_hours(pd.Series([20.0, 17.0, 7.0, -3.0])) == pytest.approx(30.0)


def test_period_shift_detects_known_shift():
    days = pd.date_range("2019-01-01", "2024-12-31", freq="D")
    rng = np.random.default_rng(0)
    temp = rng.uniform(-5, 20, len(days))
    # Load depends on the temperature bin only (flat within bins), so the per-bin
    # difference between periods is exactly the injected -500 MW shift.
    load = np.where(temp < 5, 12000, 9000) + rng.normal(0, 100, len(days)) \
        - np.where(days >= "2023-01-01", 500, 0)
    hourly = pd.DataFrame({
        "local_date": np.repeat(days.strftime("%Y-%m-%d"), 24),
        "load_mw": np.repeat(load, 24), "temperature_c": np.repeat(temp, 24),
        "is_holiday_or_eve": False})
    rows = quality.period_shift_by_temperature(hourly, ("2019-01-01", "2021-12-31"),
                                               ("2023-01-01", "2024-12-31"),
                                               bins=(-30, 5, 30))
    assert len(rows) == 2
    for r in rows:
        assert r["ci95_mw"][0] < -500 < r["ci95_mw"][1]
        assert abs(r["diff_mw"] + 500) < 30
