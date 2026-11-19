"""Fetch and parse hourly air temperature from SMHI Open Data (Meteorological Observations).

API docs: https://opendata.smhi.se/apidocs/metobs/ (no key required).
Parameter 1 = air temperature, instantaneous value once per hour.
Two periods are combined: `corrected-archive` (quality-controlled, ends a few
months back) and `latest-months` (last ~4 months, less quality control).
"""

from __future__ import annotations

import io

import pandas as pd

BASE_URL = "https://opendata-download-metobs.smhi.se/api/version/1.0"
AIR_TEMPERATURE = 1


def station_list_url(parameter: int = AIR_TEMPERATURE) -> str:
    return f"{BASE_URL}/parameter/{parameter}.json"


def data_url(station_id: int, period: str, parameter: int = AIR_TEMPERATURE) -> str:
    ext = "csv" if period == "corrected-archive" else "json"
    return f"{BASE_URL}/parameter/{parameter}/station/{station_id}/period/{period}/data.{ext}"


def resolve_stations(station_json: dict, names: list[str]) -> dict[str, int]:
    """Map exact station names to SMHI station ids. Fails if any name is missing."""
    lookup = {s["name"]: int(s["key"]) for s in station_json.get("station", [])}
    missing = [n for n in names if n not in lookup]
    if missing:
        raise KeyError(f"SMHI stations not found: {missing}")
    return {n: lookup[n] for n in names}


def parse_archive_csv(text: str) -> pd.DataFrame:
    """Parse SMHI's semicolon CSV: metadata header, then 'Datum;Tid (UTC);<param>;Kvalitet'."""
    lines = text.splitlines()
    try:
        header_idx = next(i for i, line in enumerate(lines) if line.startswith("Datum;Tid (UTC)"))
    except StopIteration as exc:
        raise ValueError("Could not find the 'Datum;Tid (UTC)' header in SMHI CSV") from exc
    body = "\n".join(lines[header_idx + 1 :])
    raw = pd.read_csv(
        io.StringIO(body), sep=";", header=None, usecols=[0, 1, 2, 3],
        names=["date", "time", "value", "quality"], dtype=str,
    )
    raw = raw.dropna(subset=["date", "time"])
    return pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime(raw["date"] + " " + raw["time"], utc=True),
            "temperature_c": pd.to_numeric(raw["value"], errors="coerce"),
            "quality": raw["quality"].str.strip(),
        }
    ).reset_index(drop=True)


def parse_latest_json(payload: dict) -> pd.DataFrame:
    """Parse the JSON returned for the `latest-months` period."""
    values = payload.get("value") or []
    return pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime([v["date"] for v in values], unit="ms", utc=True),
            "temperature_c": pd.to_numeric([v.get("value") for v in values], errors="coerce"),
            "quality": [v.get("quality") for v in values],
        }
    )


def combine_periods(archive: pd.DataFrame, latest: pd.DataFrame) -> pd.DataFrame:
    """Archive wins where both exist, because it is quality-controlled."""
    combined = pd.concat([archive.assign(source="archive"), latest.assign(source="latest")])
    combined = combined.drop_duplicates(subset="timestamp_utc", keep="first")
    return combined.sort_values("timestamp_utc").reset_index(drop=True)


def weighted_temperature(per_station: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    """Population-weighted mean temperature per hour.

    `per_station` is long format with columns timestamp_utc, station, temperature_c.
    Weights are renormalised over the stations that report in each hour, and the
    share of total weight that reported is returned so thin hours can be flagged.
    """
    df = per_station.dropna(subset=["temperature_c"]).copy()
    df["w"] = df["station"].map(weights)
    if df["w"].isna().any():
        unknown = sorted(df.loc[df["w"].isna(), "station"].unique())
        raise KeyError(f"No weight for stations: {unknown}")
    df["wt"] = df["w"] * df["temperature_c"]
    g = df.groupby("timestamp_utc")
    out = pd.DataFrame({"temperature_c": g["wt"].sum() / g["w"].sum(),
                        "weight_coverage": g["w"].sum() / sum(weights.values())})
    return out.reset_index()
