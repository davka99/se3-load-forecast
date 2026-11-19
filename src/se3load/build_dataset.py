"""Build the clean hourly SE3 dataset and the data-quality summary from raw files."""

from __future__ import annotations

import pandas as pd

from . import calendar_se, config, quality


def build(raw_cons: pd.DataFrame, raw_temp: pd.DataFrame,
          start: str = config.START, end: str = config.END) -> tuple[pd.DataFrame, dict]:
    summary: dict = {"window_utc": [start, end]}

    # ---- consumption
    switch = quality.detect_resolution_switch(raw_cons)
    load = quality.hourly_load(raw_cons)
    load["expected_periods"] = quality.expected_periods(load["timestamp_utc"], switch)
    incomplete = load["n_periods"] != load["expected_periods"]
    gaps = quality.missing_hours(load["timestamp_utc"], start, end)
    summary["load"] = {
        "raw_rows": len(raw_cons),
        "raw_duplicates": int(raw_cons["timestamp_utc"].duplicated().sum()),
        "raw_sign": {k: int(v) for k, v in
                     {"negative": (raw_cons["consumption"] < 0).sum(),
                      "zero": (raw_cons["consumption"] == 0).sum(),
                      "positive": (raw_cons["consumption"] > 0).sum(),
                      "missing": raw_cons["consumption"].isna().sum()}.items()},
        "resolution_switch_utc": str(switch) if switch is not None else None,
        "hours": len(load),
        "missing_hours": len(gaps),
        "incomplete_hours": int(incomplete.sum()),
        "min_mw": round(float(load["load_mw"].min()), 1),
        "max_mw": round(float(load["load_mw"].max()), 1),
        "max_at_utc": str(load.loc[load["load_mw"].idxmax(), "timestamp_utc"]),
        "mean_mw": round(float(load["load_mw"].mean()), 1),
    }

    # ---- temperature
    temp_long = raw_temp.copy()
    temp_long["timestamp_utc"] = pd.to_datetime(temp_long["timestamp_utc"], utc=True)
    keep_ids = set(config.TEMPERATURE_STATIONS)
    dropped = sorted(set(temp_long["station_id"]) - keep_ids)
    temp_long = temp_long[temp_long["station_id"].isin(keep_ids)]
    clean_long, rule_counts = quality.clean_station_temperature(temp_long)
    n_hours = len(pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"),
                                freq="1h", inclusive="left"))
    per_station = {}
    for name, g in clean_long.groupby("station"):
        per_station[name] = {
            "values": int(g["temperature_c"].notna().sum()),
            "coverage_pct": round(100 * g["temperature_c"].notna().sum() / n_hours, 2),
            "quality_Y": int((g["quality"] == "Y").sum()),
            "from_latest_months": int((g["source"] == "latest").sum()),
            "rule_out_of_range": int((g["temp_rule"] == "out_of_range").sum()),
            "rule_spike": int((g["temp_rule"] == "spike").sum()),
        }
    temp = quality.hourly_temperature(clean_long[["timestamp_utc", "station", "temperature_c"]],
                                      config.station_weights(), start, end)
    latest_share = (clean_long.assign(is_latest=clean_long["source"] == "latest")
                    .groupby("timestamp_utc")["is_latest"].mean())
    temp["temp_from_latest"] = temp["timestamp_utc"].map(latest_share).fillna(0.0) > 0.5
    summary["temperature"] = {
        "stations": per_station,
        "dropped_station_ids_in_raw": dropped,
        "rules": rule_counts,
        "hours_no_station": int((temp["weight_coverage"] == 0).sum()),
        "hours_imputed": int(temp["temp_imputed"].sum()),
        "hours_still_missing": int(temp["temperature_c"].isna().sum()),
        "hours_coverage_below_50pct": int((temp["weight_coverage"] < 0.5).sum()),
        "first_hour_from_latest_months": str(temp.loc[temp["temp_from_latest"],
                                                       "timestamp_utc"].min()),
    }

    # ---- join (1:1 on a complete hourly index, so no fan-out)
    n_before = len(load)
    data = load.merge(temp, on="timestamp_utc", how="left", validate="one_to_one")
    assert len(data) == n_before, "join changed row count"
    data = quality.add_local_time(data)

    # ---- load flags (never removed)
    hol = calendar_se.holiday_frame(int(start[:4]), int(end[:4]))
    hol_dates = set(hol["date"].dt.strftime("%Y-%m-%d"))
    data["is_holiday_or_eve"] = data["local_date"].isin(hol_dates)
    data = data.set_index("timestamp_utc")
    data["load_robust_z"] = quality.robust_same_hour_z(data["load_mw"])
    data = data.reset_index()
    data["flag_load_outlier"] = data["load_robust_z"].abs() > quality.LOAD_ROBUST_Z
    data["flag_load_stuck"] = quality.stuck_runs(data["load_mw"].round(3))
    data["flag_incomplete_hour"] = incomplete.to_numpy()

    out_hours = data[data["flag_load_outlier"]]
    summary["load_flags"] = {
        "outlier_hours": int(len(out_hours)),
        "outlier_hours_on_holiday_or_eve": int(out_hours["is_holiday_or_eve"].sum()),
        "outlier_days": sorted(out_hours["local_date"].unique().tolist()),
        "stuck_hours": int(data["flag_load_stuck"].sum()),
    }
    days = quality.local_day_lengths(data)
    summary["local_time"] = {"days": int(len(days)),
                             "day_length_counts": {int(k): int(v) for k, v in
                                                   days.value_counts().sort_index().items()}}

    # ---- annual context
    data["year"] = data["timestamp_utc"].dt.tz_convert(quality.LOCAL_TZ).dt.year
    annual = {}
    for year, g in data.groupby("year"):
        annual[int(year)] = {
            "hours": int(len(g)),
            "twh": round(float(g["load_mw"].sum()) / 1e6, 2),
            "mean_mw": round(float(g["load_mw"].mean()), 0),
            "peak_mw": round(float(g["load_mw"].max()), 0),
            "mean_temp_c": round(float(g["temperature_c"].mean()), 2),
            "heating_degree_hours_k": round(quality.heating_degree_hours(
                g["temperature_c"].dropna()) / 1000, 1),
        }
    summary["annual_local_year"] = annual

    cols = ["timestamp_utc", "local_time", "local_date", "local_hour", "load_mw",
            "n_periods", "temperature_c", "weight_coverage", "temp_imputed",
            "temp_from_latest", "is_holiday_or_eve", "load_robust_z", "flag_load_outlier",
            "flag_load_stuck", "flag_incomplete_hour"]
    return data[cols], summary
