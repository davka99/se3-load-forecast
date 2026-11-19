from pathlib import Path

import pandas as pd
import pytest

from se3load import smhi

FIX = Path(__file__).parent / "fixtures"


def test_parse_archive_csv_skips_metadata():
    df = smhi.parse_archive_csv((FIX / "smhi_archive_sample.csv").read_text(encoding="utf-8"))
    assert len(df) == 4
    assert df["timestamp_utc"].iloc[0] == pd.Timestamp("2024-01-08T00:00:00Z")
    assert df["temperature_c"].iloc[0] == pytest.approx(-12.4)
    assert df["temperature_c"].isna().sum() == 1
    assert list(df["quality"]) == ["G", "G", "G", "Y"]


def test_parse_archive_csv_without_header_raises():
    with pytest.raises(ValueError):
        smhi.parse_archive_csv("no;header;here\n1;2;3")


def test_parse_latest_json():
    payload = {"value": [{"date": 1704672000000, "value": "-1.5", "quality": "Y"}]}
    df = smhi.parse_latest_json(payload)
    assert df["timestamp_utc"].iloc[0] == pd.Timestamp("2024-01-08T00:00:00Z")
    assert df["temperature_c"].iloc[0] == pytest.approx(-1.5)


def test_combine_prefers_archive():
    ts = pd.to_datetime(["2024-01-01T00:00Z", "2024-01-01T01:00Z"], utc=True)
    archive = pd.DataFrame({"timestamp_utc": ts[:1], "temperature_c": [1.0], "quality": ["G"]})
    latest = pd.DataFrame({"timestamp_utc": ts, "temperature_c": [9.0, 2.0], "quality": ["Y", "Y"]})
    out = smhi.combine_periods(archive, latest)
    assert list(out["temperature_c"]) == [1.0, 2.0]
    assert list(out["source"]) == ["archive", "latest"]


def test_resolve_stations():
    js = {"station": [{"name": "A", "key": "1"}, {"name": "B", "key": "2"}]}
    assert smhi.resolve_stations(js, ["B"]) == {"B": 2}
    with pytest.raises(KeyError):
        smhi.resolve_stations(js, ["C"])


def test_weighted_temperature_renormalises_when_station_missing():
    ts = pd.Timestamp("2024-01-01T00:00Z")
    ts2 = pd.Timestamp("2024-01-01T01:00Z")
    long = pd.DataFrame({
        "timestamp_utc": [ts, ts, ts2, ts2],
        "station": ["A", "B", "A", "B"],
        "temperature_c": [0.0, 10.0, 4.0, float("nan")],
    })
    out = smhi.weighted_temperature(long, {"A": 3.0, "B": 1.0})
    assert out["temperature_c"].iloc[0] == pytest.approx(2.5)
    assert out["temperature_c"].iloc[1] == pytest.approx(4.0)
    assert out["weight_coverage"].iloc[1] == pytest.approx(0.75)
