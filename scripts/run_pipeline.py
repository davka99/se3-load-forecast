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
