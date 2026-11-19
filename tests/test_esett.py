import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from se3load import esett

FIX = Path(__file__).parent / "fixtures"


def load_fixture():
    return json.loads((FIX / "esett_consumption_sample.json").read_text())


def test_parse_uses_utc_and_keeps_nan():
    df = esett.parse_consumption(load_fixture())
    assert str(df["timestamp_utc"].dt.tz) == "UTC"
    assert df["timestamp_utc"].iloc[0] == pd.Timestamp("2025-10-06T00:00:00Z")
    assert df["consumption"].isna().sum() == 1


def test_parse_unknown_schema_fails_loudly():
    with pytest.raises(KeyError):
        esett.parse_consumption([{"time": "2025-01-01", "value": 1.0}])


def test_to_hourly_energy_sums_and_counts_periods():
    hourly = esett.to_hourly(esett.parse_consumption(load_fixture()), value_kind="energy")
    first = hourly.iloc[0]
    assert first["load_mw"] == pytest.approx(12000.0)
    assert first["n_periods"] == 4
    assert hourly.iloc[1]["n_periods"] == 1  # incomplete hour is visible, not hidden


def test_to_hourly_power_means():
    hourly = esett.to_hourly(esett.parse_consumption(load_fixture()), value_kind="power")
    assert hourly.iloc[0]["load_mw"] == pytest.approx(3000.0)


def test_date_chunks_cover_window_without_gaps():
    chunks = esett.date_chunks("2024-01-01", "2024-03-15", days=31)
    assert chunks[0][0].isoformat().startswith("2024-01-01")
    assert chunks[-1][1].isoformat().startswith("2024-03-15")
    for (a, b), (c, d) in zip(chunks, chunks[1:]):
        assert b == c
    assert all(b > a for a, b in chunks)


def test_build_params_rejects_unknown_zone():
    with pytest.raises(KeyError):
        esett.build_params("SE5", *esett.date_chunks("2024-01-01", "2024-01-02")[0])


def test_build_params_se3():
    p = esett.build_params("SE3", *esett.date_chunks("2024-01-01", "2024-01-02")[0])
    assert p["mba"] == "10Y1001A1001A46L"
    assert p["start"] == "2024-01-01T00:00:00.000Z"
    assert np.isfinite(len(p))
