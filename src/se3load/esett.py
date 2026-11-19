"""Fetch and parse metered electricity consumption from the eSett Open Data API.

eSett is the Nordic imbalance settlement operator. Its open data API
(https://api.opendata.esett.com, no key required) publishes settled consumption
per metering balance area (MBA). The Swedish bidding zones SE1-SE4 are MBAs
identified by their EIC codes.

The exact response schema is verified by the `probe` pipeline stage and recorded
in docs/decisions.md; the parser therefore accepts a small set of known field
names and fails loudly on anything else instead of guessing.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

import pandas as pd

BASE_URL = "https://api.opendata.esett.com"
CONSUMPTION_PATH = "/EXP15/Consumption"

MBA_EIC = {
    "SE1": "10Y1001A1001A44P",
    "SE2": "10Y1001A1001A45N",
    "SE3": "10Y1001A1001A46L",
    "SE4": "10Y1001A1001A47J",
}

TIMESTAMP_KEYS = ("timestampUTC", "timestampUtc", "timestamp_utc")
TOTAL_KEYS = ("total", "totalConsumption")


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def date_chunks(start: str, end: str, days: int = 31) -> list[tuple[datetime, datetime]]:
    """Split [start, end) into consecutive UTC windows of at most `days` days."""
    lo = pd.Timestamp(start, tz="UTC").to_pydatetime()
    hi = pd.Timestamp(end, tz="UTC").to_pydatetime()
    if hi <= lo:
        raise ValueError("end must be after start")
    chunks = []
    cur = lo
    while cur < hi:
        nxt = min(cur + timedelta(days=days), hi)
        chunks.append((cur, nxt))
        cur = nxt
    return chunks


def build_params(zone: str, start: datetime, end: datetime) -> dict[str, str]:
    if zone not in MBA_EIC:
        raise KeyError(f"Unknown bidding zone {zone!r}; expected one of {sorted(MBA_EIC)}")
    return {"mba": MBA_EIC[zone], "start": _iso(start), "end": _iso(end)}


def fetch_consumption_raw(
    zone: str,
    start: str,
    end: str,
    session=None,
    chunk_days: int = 31,
    pause_s: float = 0.5,
) -> list[dict]:
    """Download raw consumption records for a zone, chunked by month-sized windows."""
    import requests  # imported lazily so parsing can be tested without network deps

    sess = session or requests.Session()
    records: list[dict] = []
    for lo, hi in date_chunks(start, end, chunk_days):
        resp = sess.get(BASE_URL + CONSUMPTION_PATH, params=build_params(zone, lo, hi), timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list):
            raise ValueError(f"Expected a JSON list from eSett, got {type(payload).__name__}")
        records.extend(payload)
        time.sleep(pause_s)
    return records


def _pick_key(record: dict, candidates: Iterable[str]) -> str:
    for key in candidates:
        if key in record:
            return key
    raise KeyError(f"None of {list(candidates)} in record keys {sorted(record)}")


def parse_consumption(records: list[dict]) -> pd.DataFrame:
    """Turn raw eSett records into a tidy frame: timestamp_utc, consumption.

    Values are returned exactly as published (unit and sign unchanged); the
    data-quality step documents and converts them. Missing totals stay NaN.
    """
    if not records:
        return pd.DataFrame({"timestamp_utc": pd.Series(dtype="datetime64[ns, UTC]"),
                             "consumption": pd.Series(dtype="float64")})
    ts_key = _pick_key(records[0], TIMESTAMP_KEYS)
    total_key = _pick_key(records[0], TOTAL_KEYS)
    df = pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime([r.get(ts_key) for r in records], utc=True),
            "consumption": pd.to_numeric([r.get(total_key) for r in records], errors="coerce"),
        }
    )
    return df.sort_values("timestamp_utc").reset_index(drop=True)


def to_hourly(df: pd.DataFrame, value_kind: str = "energy") -> pd.DataFrame:
    """Aggregate sub-hourly settlement periods to hours (UTC).

    value_kind="energy": values are MWh per period, so hourly MWh is the sum.
    value_kind="power":  values are average MW per period, so hourly MW is the mean.
    Either way the result is numerically average MW over the hour (= MWh/h).
    `n_periods` is kept so incomplete hours can be flagged instead of silently used.
    """
    if value_kind not in {"energy", "power"}:
        raise ValueError("value_kind must be 'energy' or 'power'")
    hourly = df.set_index("timestamp_utc")["consumption"].resample("1h")
    agg = hourly.sum(min_count=1) if value_kind == "energy" else hourly.mean()
    out = pd.DataFrame({"load_mw": agg, "n_periods": hourly.count()})
    return out.reset_index()
