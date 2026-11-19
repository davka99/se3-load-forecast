"""Pipeline entry point. Usage: python scripts/run_pipeline.py <stage> [<stage> ...]

Stages
  probe  Download a small sample from each source and record the schema in
         data/raw/_probe/ (used to verify field names, units and station names).
  fetch  Download the full study window to data/raw/.

Later milestones add: quality, features, backtest, report.
Network access is only needed for `probe` and `fetch`.
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

    # SMHI: station list and candidate matches for the configured names.
    try:
        stations = sess.get(smhi.station_list_url(), timeout=60).json()
        all_st = stations.get("station", [])
        candidates = {}
        for name in config.TEMPERATURE_STATIONS:
            stem = name.split()[0].split("-")[0].lower()
            candidates[name] = [
                {"name": s["name"], "key": s["key"], "active": s.get("active"),
                 "from": s.get("from"), "to": s.get("to")}
                for s in all_st if stem in s["name"].lower()
            ]
        summary["smhi_station_candidates"] = candidates
        try:
            ids = smhi.resolve_stations(stations, list(config.TEMPERATURE_STATIONS))
            summary["smhi_resolved"] = ids
            first_id = next(iter(ids.values()))
            csv_text = sess.get(smhi.data_url(first_id, "corrected-archive"), timeout=120).text
            (out / "smhi_archive_head.csv").write_text("\n".join(csv_text.splitlines()[:20]),
                                                       encoding="utf-8")
            latest = sess.get(smhi.data_url(first_id, "latest-months"), timeout=60).json()
            summary["smhi_latest_keys"] = sorted(latest)
            summary["smhi_latest_n"] = len(latest.get("value") or [])
        except KeyError as exc:
            summary["smhi_resolve_error"] = repr(exc)
    except Exception as exc:
        summary["smhi"] = {"error": repr(exc)}

    _write_json(out / "probe_summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str)[:4000])


def fetch() -> None:
    sess = _session()
    config.DATA_RAW.mkdir(parents=True, exist_ok=True)

    recs = esett.fetch_consumption_raw(config.BIDDING_ZONE, config.START, config.END, session=sess)
    cons = esett.parse_consumption(recs)
    cons.to_csv(config.DATA_RAW / f"esett_consumption_{config.BIDDING_ZONE.lower()}.csv.gz",
                index=False)
    print(f"eSett: {len(cons):,} rows, {cons['timestamp_utc'].min()} .. {cons['timestamp_utc'].max()}")

    stations = sess.get(smhi.station_list_url(), timeout=60).json()
    ids = smhi.resolve_stations(stations, list(config.TEMPERATURE_STATIONS))
    frames = []
    for name, sid in ids.items():
        archive = smhi.parse_archive_csv(sess.get(smhi.data_url(sid, "corrected-archive"),
                                                  timeout=300).text)
        latest = smhi.parse_latest_json(sess.get(smhi.data_url(sid, "latest-months"),
                                                 timeout=60).json())
        combined = smhi.combine_periods(archive, latest)
        combined = combined[combined["timestamp_utc"] >= config.START]
        frames.append(combined.assign(station=name, station_id=sid))
        print(f"SMHI {name} ({sid}): {len(combined):,} rows")
    import pandas as pd

    temps = pd.concat(frames, ignore_index=True)
    temps.to_csv(config.DATA_RAW / "smhi_air_temperature.csv.gz", index=False)
    _write_json(config.DATA_RAW / "fetch_manifest.json", {
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": [config.START, config.END],
        "esett_rows": len(cons),
        "smhi_station_ids": ids,
        "smhi_rows": len(temps),
    })


STAGES = {"probe": probe, "fetch": fetch}


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
