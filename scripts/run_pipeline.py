"""Pipeline entry point. Usage: python scripts/run_pipeline.py <stage> [<stage> ...]

Stages
  probe  Download a small sample from each source and record the schema in
         data/raw/_probe/ (used to verify field names, units and station names).
  fetch      Download the full study window to data/raw/.
  reconcile  Download annual consumption for all four Swedish zones (eSett) to
             data/raw/esett_annual_by_zone.csv for reconciliation.
  quality    Build data/processed/se3_hourly.csv.gz, the data-quality summary
             (reports/data_quality_summary.json) and overview figures.
  explore    MSTL decomposition of daily load (reports/exploration_summary.json,
             reports/figures/decomposition.png). Needs statsmodels.
  select     D11: compare training-window options on the validation year
             (reports/d11_training_window.json).

Later milestones add: backtest, report.
Network access is only needed for `probe`, `fetch` and `reconcile`.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from se3load import config, esett, smhi  # noqa: E402


def _session():
    import requests

    s = requests.Session()
    s.headers["User-Agent"] = "se3-load-forecast (portfolio project; github.com/davka99)"
    return s


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def probe() -> None:
    out = config.DATA_RAW / "_probe"
    sess = _session()
    summary: dict = {"run_at_utc": datetime.now(timezone.utc).isoformat()}

    # eSett: one week in winter and one week after the 15-minute MTU switch.
    for label, start, end in [("winter_2024", "2024-01-08", "2024-01-15"),
                              ("autumn_2025", "2025-10-06", "2025-10-13")]:
        try:
            recs = esett.fetch_consumption_raw(config.BIDDING_ZONE, start, end, session=sess)
            _write_json(out / f"esett_{label}_sample.json", recs[:8])
            ts_key = next((k for k in esett.TIMESTAMP_KEYS if recs and k in recs[0]), None)
            summary[f"esett_{label}"] = {
                "n_records": len(recs),
                "keys": sorted(recs[0]) if recs else [],
                "first_timestamp": recs[0].get(ts_key) if ts_key else None,
                "last_timestamp": recs[-1].get(ts_key) if ts_key else None,
            }
            if recs:
                parsed = esett.parse_consumption(recs)
                summary[f"esett_{label}"]["consumption_describe"] = (
                    parsed["consumption"].describe().round(3).to_dict())
                step = parsed["timestamp_utc"].diff().dropna().value_counts().head(3)
                summary[f"esett_{label}"]["timestep_counts"] = {str(k): int(v) for k, v in step.items()}
        except Exception as exc:  # record and continue: the probe is diagnostic
            summary[f"esett_{label}"] = {"error": repr(exc)}

    # SMHI: verify the pinned station ids and record the archive header format.
    try:
        stations = sess.get(smhi.station_list_url(), timeout=60).json()
        expected = {sid: name for sid, (name, _) in config.TEMPERATURE_STATIONS.items()}
        meta = smhi.verify_station_ids(stations, expected)
        summary["smhi_verified"] = {sid: m["name"] for sid, m in meta.items()}
        first_id = next(iter(expected))
        csv_text = sess.get(smhi.data_url(first_id, "corrected-archive"), timeout=120).text
        (out / "smhi_archive_head.csv").write_text("\n".join(csv_text.splitlines()[:20]),
                                                   encoding="utf-8")
        latest = sess.get(smhi.data_url(first_id, "latest-months"), timeout=60).json()
        summary["smhi_latest_keys"] = sorted(latest)
        summary["smhi_latest_n"] = len(latest.get("value") or [])
    except Exception as exc:
        summary["smhi"] = {"error": repr(exc)}

    _write_json(out / "probe_summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str)[:4000])


def fetch() -> None:
    import pandas as pd

    sess = _session()
    config.DATA_RAW.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"run_at_utc": datetime.now(timezone.utc).isoformat(),
                      "window_utc": [config.START, config.END]}

    # eSett consumption, stored exactly as published (negative MWh per period).
    recs = esett.fetch_consumption_raw(config.BIDDING_ZONE, config.START, config.END, session=sess)
    cons = esett.parse_consumption(recs)
    path = config.DATA_RAW / f"esett_consumption_{config.BIDDING_ZONE.lower()}.csv.gz"
    cons.to_csv(path, index=False)
    manifest["esett"] = {
        "file": path.name,
        "endpoint": esett.BASE_URL + esett.CONSUMPTION_PATH,
        "mba": esett.MBA_EIC[config.BIDDING_ZONE],
        "rows": len(cons),
        "first_utc": str(cons["timestamp_utc"].min()),
        "last_utc": str(cons["timestamp_utc"].max()),
        "duplicated_timestamps": int(cons["timestamp_utc"].duplicated().sum()),
        "sign": esett.sign_summary(cons),
        "resolution_by_year": esett.resolution_by_year(cons),
        "annual_sum_mwh_as_published": {
            int(y): round(float(v), 1)
            for y, v in cons.groupby(cons["timestamp_utc"].dt.year)["consumption"].sum().items()
        },
    }
    print(json.dumps(manifest["esett"], indent=2, default=str))

    # SMHI temperature per pinned station: archive + latest months.
    stations = sess.get(smhi.station_list_url(), timeout=60).json()
    expected = {sid: name for sid, (name, _) in config.TEMPERATURE_STATIONS.items()}
    smhi.verify_station_ids(stations, expected)
    frames, per_station = [], {}
    for sid, name in expected.items():
        archive = smhi.parse_archive_csv(
            sess.get(smhi.data_url(sid, "corrected-archive"), timeout=300).text)
        latest = smhi.parse_latest_json(
            sess.get(smhi.data_url(sid, "latest-months"), timeout=60).json())
        combined = smhi.combine_periods(archive, latest)
        combined = combined[(combined["timestamp_utc"] >= pd.Timestamp(config.START, tz="UTC"))
                            & (combined["timestamp_utc"] < pd.Timestamp(config.END, tz="UTC"))]
        frames.append(combined.assign(station=name, station_id=sid))
        per_station[sid] = {
            "name": name,
            "rows": len(combined),
            "first_utc": str(combined["timestamp_utc"].min()),
            "last_utc": str(combined["timestamp_utc"].max()),
            "archive_last_utc": str(archive["timestamp_utc"].max()),
            "missing_values": int(combined["temperature_c"].isna().sum()),
            "by_source": {k: int(v) for k, v in combined["source"].value_counts().items()},
        }
        print(f"SMHI {name} ({sid}): {len(combined):,} rows")
    temps = pd.concat(frames, ignore_index=True)
    path = config.DATA_RAW / "smhi_air_temperature.csv.gz"
    temps.to_csv(path, index=False)
    manifest["smhi"] = {"file": path.name, "rows": len(temps), "stations": per_station}

    _write_json(config.DATA_RAW / "fetch_manifest.json", manifest)


def reconcile() -> None:
    import pandas as pd

    from se3load import quality as q

    sess = _session()
    rows = []
    for zone in sorted(esett.MBA_EIC):
        raw = esett.parse_consumption(
            esett.fetch_consumption_raw(zone, config.START, "2026-01-01", session=sess))
        local_year = raw["timestamp_utc"].dt.tz_convert(q.LOCAL_TZ).dt.year
        for year, g in raw.groupby(local_year):
            rows.append({"zone": zone, "year": int(year), "rows": len(g),
                         "twh": round(-float(g["consumption"].sum()) / 1e6, 3),
                         "positive_values": int((g["consumption"] > 0).sum())})
        print(f"{zone}: {len(raw):,} rows")
    out = pd.DataFrame(rows)
    out.to_csv(config.DATA_RAW / "esett_annual_by_zone.csv", index=False)
    print(out.pivot(index="year", columns="zone", values="twh"))


def _reconciliation_table() -> list[dict]:
    import pandas as pd

    zones_path = config.DATA_RAW / "esett_annual_by_zone.csv"
    ref_path = config.PROJECT_ROOT / "data" / "reference" / "national_electricity_use.csv"
    if not zones_path.exists():
        return []
    zones = pd.read_csv(zones_path)
    wide = zones.pivot(index="year", columns="zone", values="twh")
    wide = wide[[c for c in ["SE1", "SE2", "SE3", "SE4"] if c in wide]]
    ref = pd.read_csv(ref_path).set_index("year")
    rows = []
    for year, r in wide.iterrows():
        if year < 2019 or year > 2025:
            continue
        total = float(r.sum())
        row = {"year": int(year), **{z: round(float(v), 2) for z, v in r.items()},
               "esett_total_twh": round(total, 2),
               "se3_share_pct": round(100 * float(r["SE3"]) / total, 1)}
        if year in ref.index:
            incl = float(ref.loc[year, "use_incl_losses_twh"])
            excl = incl - float(ref.loc[year, "transmission_losses_twh"])
            row.update({"published_incl_losses_twh": incl,
                        "published_excl_losses_twh": round(excl, 1),
                        "esett_vs_excl_losses_pct": round(100 * (total - excl) / excl, 1),
                        "esett_vs_incl_losses_pct": round(100 * (total - incl) / incl, 1)})
        rows.append(row)
    return rows


def quality_stage() -> None:
    import pandas as pd

    from se3load import build_dataset, figures
    from se3load import quality as q

    cons = pd.read_csv(config.DATA_RAW / f"esett_consumption_{config.BIDDING_ZONE.lower()}.csv.gz")
    cons["timestamp_utc"] = pd.to_datetime(cons["timestamp_utc"], utc=True)
    temp = pd.read_csv(config.DATA_RAW / "smhi_air_temperature.csv.gz")
    data, summary = build_dataset.build(cons, temp)
    summary["reconciliation"] = _reconciliation_table()
    summary["period_shift_by_temperature"] = q.period_shift_by_temperature(
        data, ("2019-01-01", "2021-12-31"), ("2023-01-01", "2026-10-31"))

    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    config.FIGURES.mkdir(parents=True, exist_ok=True)
    data.to_csv(config.DATA_PROCESSED / "se3_hourly.csv.gz", index=False)
    _write_json(config.REPORTS / "data_quality_summary.json", summary)
    figures.overview(data, config.FIGURES / "overview_load_temperature.png")
    figures.load_vs_temperature(data, config.FIGURES / "load_vs_temperature.png")
    print(json.dumps({k: summary[k] for k in ("load", "load_flags", "reconciliation")},
                     indent=2, default=str))


def _processed():
    import pandas as pd

    d = pd.read_csv(config.DATA_PROCESSED / "se3_hourly.csv.gz")
    d["timestamp_utc"] = pd.to_datetime(d["timestamp_utc"], utc=True)
    return d


def explore_stage() -> None:
    from se3load import explore, figures

    daily = explore.daily_load(_processed())
    dec = explore.mstl_daily(daily)
    strengths = {"trend": explore.trend_strength(dec),
                 "seasonal_7": explore.seasonal_strength(dec, "seasonal_7"),
                 "seasonal_365": explore.seasonal_strength(dec, "seasonal_365")}
    weekly = dec["seasonal_7"].groupby(dec.index.dayofweek).mean()
    trend_q = dec["trend"].resample("QS").mean()
    summary = {
        "daily_days": int(daily.notna().sum()),
        "strengths": {k: round(v, 3) for k, v in strengths.items()},
        "weekly_component_by_dow_mw": {int(k): round(float(v), 1) for k, v in weekly.items()},
        "yearly_component_range_mw": [round(float(dec["seasonal_365"].min()), 0),
                                      round(float(dec["seasonal_365"].max()), 0)],
        "trend_quarterly_mw": {str(k.date()): round(float(v), 0) for k, v in trend_q.items()},
        "resid_sd_mw": round(float(dec["resid"].std()), 1),
    }
    _write_json(config.REPORTS / "exploration_summary.json", summary)
    figures.decomposition(dec, strengths, config.FIGURES / "decomposition.png")
    print(json.dumps(summary, indent=2))


def select_stage() -> None:
    from se3load import features, selection

    feat = features.build_features(_processed())
    rows, comparisons = selection.evaluate(feat, seed=config.SEED)
    result = {"rule": selection.__doc__.strip(), "train_end": selection.TRAIN_END,
              "validation": [selection.VAL_START, selection.VAL_END],
              "half_life_days": selection.HALF_LIFE_DAYS,
              "results": rows, "chosen": selection.choose(rows),
              "paired_bootstrap_vs_chosen": comparisons}
    _write_json(config.REPORTS / "d11_training_window.json", result)
    print(json.dumps(result, indent=2))


STAGES = {"probe": probe, "fetch": fetch, "reconcile": reconcile, "quality": quality_stage,
          "explore": explore_stage, "select": select_stage}


def main(argv: list[str]) -> int:
    stages = [s for a in argv for s in a.replace(",", " ").split()]
    if not stages:
        print(__doc__)
        return 2
    unknown = [s for s in stages if s not in STAGES]
    if unknown:
        print(f"Unknown stage(s): {unknown}. Known: {sorted(STAGES)}")
        return 2
    for stage in stages:
        print(f"== stage: {stage}")
        STAGES[stage]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
