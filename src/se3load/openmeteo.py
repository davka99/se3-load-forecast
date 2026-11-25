"""Archived day-ahead temperature forecasts from Open-Meteo's Previous Runs API.

API: https://open-meteo.com/en/docs/previous-runs-api (no key, CC BY 4.0, data from
January 2024). `temperature_2m_previous_dayN` is a continuous hourly series in which
each value comes from the model run made about N days before that hour.

Why two lead times (D20): the operational forecast for day D is issued at 12:00 local
time on D-1, so the lead time is 12-36 hours depending on the hour. `previous_day1`
uses runs made about 24 h before each hour, so for the late hours of D it uses runs
made *after* the 12:00 deadline (slightly optimistic). `previous_day2` uses runs made
about 48 h before each hour, all of them before the deadline (slightly pessimistic).
The operational error lies between the two.
"""

from __future__ import annotations

import pandas as pd

BASE_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
LEAD_DAYS = (1, 2)
# best_match lets Open-Meteo pick the best regional model for Sweden; the fetch records
# which model answered, and falls back to ECMWF IFS if best_match has gaps.
MODELS = ("best_match", "ecmwf_ifs025")


def variable(lead_day: int) -> str:
    return f"temperature_2m_previous_day{lead_day}"


def params(lats: list[float], lons: list[float], start_date: str, end_date: str,
           model: str = "best_match") -> dict:
    """Query parameters for one request covering several stations."""
    if len(lats) != len(lons) or not lats:
        raise ValueError("lats and lons must be non-empty and of equal length")
    return {
        "latitude": ",".join(f"{x:.4f}" for x in lats),
        "longitude": ",".join(f"{x:.4f}" for x in lons),
        "hourly": ",".join(variable(d) for d in LEAD_DAYS),
        "start_date": start_date,
        "end_date": end_date,
        "timezone": "GMT",
        "models": model,
    }


def parse(payload: dict | list, station_names: list[str]) -> pd.DataFrame:
    """Long frame: timestamp_utc, station, lead_day, temperature_c.

    Open-Meteo returns one object for one location and a list for several, in the
    order the coordinates were given.
    """
    items = payload if isinstance(payload, list) else [payload]
    if len(items) != len(station_names):
        raise ValueError(f"expected {len(station_names)} locations, got {len(items)}")
    frames = []
    for name, item in zip(station_names, items):
        if "error" in item and item.get("error"):
            raise ValueError(f"Open-Meteo error for {name}: {item.get('reason')}")
        hourly = item["hourly"]
        ts = pd.to_datetime(hourly["time"], utc=True)
        for d in LEAD_DAYS:
            values = hourly.get(variable(d))
            if values is None:
                raise KeyError(f"{variable(d)} missing for {name}")
            frames.append(pd.DataFrame({"timestamp_utc": ts, "station": name, "lead_day": d,
                                        "temperature_c": pd.to_numeric(pd.Series(values),
                                                                       errors="coerce")}))
    return pd.concat(frames, ignore_index=True)


def station_coordinates(station_json: dict, ids: list[int]) -> dict[int, tuple[float, float]]:
    """Latitude/longitude per pinned SMHI station id from SMHI's station list."""
    by_id = {int(s["key"]): s for s in station_json.get("station", [])}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise KeyError(f"stations not in SMHI list: {missing}")
    return {i: (float(by_id[i]["latitude"]), float(by_id[i]["longitude"])) for i in ids}
