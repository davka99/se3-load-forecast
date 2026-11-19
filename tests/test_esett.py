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


def test_sign_summary_and_flip():
    df = esett.parse_consumption([
        {"timestampUTC": "2024-01-01T00:00:00Z", "total": -10.0},
        {"timestampUTC": "2024-01-01T00:15:00Z", "total": 2.0},
        {"timestampUTC": "2024-01-01T00:30:00Z", "total": None},
    ])
    assert esett.sign_summary(df) == {"negative": 1, "zero": 0, "positive": 1, "missing": 1}
    flipped = esett.to_positive_consumption(df)
    assert list(flipped["consumption"].iloc[:2]) == [10.0, -2.0]  # wrong sign stays visible
    assert df["consumption"].iloc[0] == -10.0  # input not mutated


def test_resolution_by_year_detects_switch():
    ts = list(pd.date_range("2022-12-31T21:00Z", periods=3, freq="1h")) + \
        list(pd.date_range("2023-01-01T00:00Z", periods=8, freq="15min"))
    df = pd.DataFrame({"timestamp_utc": ts, "consumption": -1.0})
    res = esett.resolution_by_year(df)
    assert res[2022] == {"0 days 01:00:00": 2}
    assert max(res[2023], key=res[2023].get) == "0 days 00:15:00"


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._payload = status, payload

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return self._payload


class _Sess:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self.responses.pop(0)


def test_retry_on_server_error_then_success():
    sess = _Sess([_Resp(503), _Resp(200, [{"x": 1}])])
    assert esett._get_json_with_retry(sess, "u", {}, retries=3, backoff_s=0) == [{"x": 1}]
    assert sess.calls == 2


def test_no_retry_on_client_error():
    import requests
    sess = _Sess([_Resp(400), _Resp(200, [])])
    with pytest.raises(requests.HTTPError):
        esett._get_json_with_retry(sess, "u", {}, retries=3, backoff_s=0)
    assert sess.calls == 1
