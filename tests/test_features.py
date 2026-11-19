from datetime import date

import numpy as np
import pandas as pd
import pytest

from se3load import features, quality


def hourly_frame(start="2025-10-01", end="2025-11-10", load=None):
    """Processed-shaped frame where load_mw encodes its own UTC hour index, so a
    lag value tells us exactly which hour it was read from."""
    ts = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"),
                       freq="1h", inclusive="left")
    df = pd.DataFrame({"timestamp_utc": ts})
    df = quality.add_local_time(df)
    df["load_mw"] = np.arange(len(df), dtype=float) if load is None else load
    df["temperature_c"] = 5.0
    return df


def test_load_lags_never_read_after_end_of_d_minus_2_including_dst():
    # Window contains the autumn DST change (2025-10-26) where a 48 h UTC lag would leak.
    df = hourly_frame("2025-10-01", "2025-11-10")
    feat = features.build_features(df)
    hour_index_of = dict(zip(df["timestamp_utc"], df["load_mw"]))
    ts_of_index = {v: k for k, v in hour_index_of.items()}
    checked = 0
    for _, row in feat.dropna(subset=["load_d2_same_hour"]).iterrows():
        target = date.fromisoformat(row["local_date"])
        cutoff_local = pd.Timestamp(target - pd.Timedelta(days=1), tz="Europe/Stockholm")
        for col in ("load_d2_same_hour", "load_d7_same_hour"):
            v = row[col]
            if np.isnan(v):
                continue
            # averaged duplicate hours give .5 indices: check both neighbours
            for idx in {np.floor(v), np.ceil(v)}:
                assert ts_of_index[idx] < cutoff_local, (row["local_date"], col)
                checked += 1
    assert checked > 1000


def test_lag_same_local_hour_and_autumn_duplicate_averaged():
    df = hourly_frame("2025-10-20", "2025-11-01")
    feat = features.build_features(df)
    # Target 2025-10-28 02:00 local -> source 2025-10-26 02:00 local, which occurs twice.
    row = feat[(feat["local_date"] == "2025-10-28") & (feat["local_hour"] == 2)].iloc[0]
    src = df[(df["local_date"] == "2025-10-26") & (df["local_hour"] == 2)]["load_mw"]
    assert len(src) == 2
    assert row["load_d2_same_hour"] == pytest.approx(src.mean())


def test_spring_dst_missing_hour_falls_back_to_next_hour():
    df = hourly_frame("2025-03-25", "2025-04-05")
    feat = features.build_features(df)
    row = feat[(feat["local_date"] == "2025-04-01") & (feat["local_hour"] == 2)].iloc[0]
    src = df[(df["local_date"] == "2025-03-30") & (df["local_hour"] == 3)]["load_mw"].iloc[0]
    assert row["load_d2_same_hour"] == src


def test_calendar_features_known_days():
    df = hourly_frame("2025-05-26", "2025-06-24")
    cal = features.calendar_features(df)
    by_date = pd.concat([df["local_date"], cal], axis=1).drop_duplicates("local_date") \
        .set_index("local_date")
    assert by_date.loc["2025-05-29", "is_public_holiday"]          # Kristi himmelsfärd
    assert by_date.loc["2025-05-30", "is_bridge_day"]              # klämdag
    assert by_date.loc["2025-05-30", "daytype"] == "bridge"
    assert by_date.loc["2025-06-20", "is_de_facto_holiday"]        # Midsommarafton
    assert by_date.loc["2025-06-20", "daytype"] == "sunday_or_holiday"
    assert by_date.loc["2025-06-02", "daytype"] == "monday"
    assert by_date.loc["2025-06-07", "daytype"] == "saturday"


def test_temperature_features():
    df = hourly_frame("2025-01-01", "2025-01-03")
    df["temperature_c"] = np.r_[np.full(24, -5.0), np.full(24, 25.0)]
    t = features.temperature_features(df)
    assert t["hdd"].iloc[0] == pytest.approx(20.0)
    assert t["cdd"].iloc[-1] == pytest.approx(5.0)
    assert t["hdd"].iloc[-1] == 0
    # smoothed temperature lags behind a step change
    assert -5 < t["temp_ewm48"].iloc[-1] < 25


def test_regime_boundaries():
    s = pd.Series(["2021-12-31", "2022-01-01", "2022-12-31", "2023-01-01"])
    assert list(features.regime(s)) == ["pre_2022", "y2022", "y2022", "post_2022"]


def test_linear_design_shapes_and_options():
    df = hourly_frame("2025-01-01", "2025-02-01")
    feat = features.build_features(df)
    X = features.linear_design(feat)
    Xr = features.linear_design(feat, regime_terms=True)
    Xn = features.linear_design(feat, use_lags=False)
    assert len(X) == len(feat)
    assert set(Xr.columns) - set(X.columns) == {"regime_y2022", "regime_y2022_x_hdd",
                                                "regime_post_2022", "regime_post_2022_x_hdd"}
    assert not any(c.startswith("load_") for c in Xn.columns)
    assert sum(c.startswith("hdd_h") for c in X.columns) == 24


def test_naive_48h_utc_lag_would_leak_on_autumn_dst_day():
    """Documents why lags are built on local dates: a fixed 48 h UTC shift reads a
    value from D-1 for the last hour of the day after the autumn DST change."""
    df = hourly_frame("2025-10-20", "2025-11-01")
    target = df[(df["local_date"] == "2025-10-27") & (df["local_hour"] == 23)].iloc[0]
    source_utc = target["timestamp_utc"] - pd.Timedelta(hours=48)
    source_local_date = source_utc.tz_convert("Europe/Stockholm").date().isoformat()
    # The D-2 rule requires the source to be on 2025-10-25 or earlier.
    assert source_local_date == "2025-10-26"  # i.e. D-1: a leak the local-date lag avoids
