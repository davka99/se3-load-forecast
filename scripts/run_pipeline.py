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
  baselines  Rolling-origin backtest of the seasonal-naive baselines on the
             validation year (reports/baselines_validation.json).
  regression Linear model backtest on the validation year, temperature sensitivity
             (MW per degC, HAC CI) and a Stockholm-only temperature check
             (reports/regression_validation.json). Needs statsmodels.
  gbm        Calendar variants (D17) for the linear model, gradient-boosting quantile
             model and a linear+GBM average, all on the validation year
             (reports/gbm_validation.json).
  evaluate   M8: the held-out backtest year, run once for all pre-registered models
             (D19): reports/backtest_results.json, predictions and figures.

The held-out backtest year is only evaluated in M8.
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


def baselines_stage() -> None:
    from se3load import backtest, features, selection

    data = _processed()
    feat = features.build_features(data)
    scale = backtest.mase_scale_from_history(data, selection.VAL_START)
    out = {"window": [selection.VAL_START, selection.VAL_END],
           "mase_scale_mw": round(scale, 1),
           "mase_scale_note": "in-sample MAE of the 168 h seasonal naive, 2019-01-01 to "
                              "the day before the window", "models": {}}
    preds = []
    for model in backtest.baselines():
        res = backtest.run(feat, model, selection.VAL_START, selection.VAL_END)
        p = res.predictions
        summ = backtest.summarise(p, scale)
        p["abs_err"] = (p["forecast"] - p["actual"]).abs()
        summ["mae_by_month_mw"] = {str(k): round(float(v), 1) for k, v in
                                   p.groupby(p["local_date"].str[:7])["abs_err"].mean().items()}
        out["models"][model.name] = summ
        preds.append(p.drop(columns="abs_err"))
    path = config.REPORTS / "predictions"
    path.mkdir(parents=True, exist_ok=True)
    pd_ = __import__("pandas")
    pd_.concat(preds).to_csv(path / "validation_baselines.csv.gz", index=False)
    _write_json(config.REPORTS / "baselines_validation.json", out)
    print(json.dumps(out, indent=2))


def regression_stage() -> None:
    import pandas as pd

    from se3load import backtest, build_dataset, features, metrics, models, selection
    from se3load import sensitivity as sens

    data = _processed()
    feat = features.build_features(data)
    scale = backtest.mase_scale_from_history(data, selection.VAL_START)
    win = (selection.VAL_START, selection.VAL_END)
    out: dict = {"window": list(win), "mase_scale_mw": round(scale, 1)}

    res = backtest.run(feat, models.LinearModel(), *win)
    backtest.assert_no_leakage(res)
    p = res.predictions
    out["linear"] = backtest.summarise(p, scale)
    out["linear"]["fits"] = res.fits

    base = pd.read_csv(config.REPORTS / "predictions" / "validation_baselines.csv.gz")
    sn = base[base["model"] == "seasonal_naive_d7"].set_index("timestamp_utc")["forecast"]
    p["timestamp_key"] = p["timestamp_utc"].astype(str).str.replace(" ", "T")
    sn.index = sn.index.astype(str).str.replace(" ", "T")
    sn_aligned = sn.reindex(p["timestamp_key"]).to_numpy()
    weeks = pd.to_datetime(p["local_date"]).dt.strftime("%G-%V").to_numpy()
    cmp = metrics.block_bootstrap_mae_diff(p["actual"], p["forecast"], sn_aligned, weeks,
                                           seed=config.SEED)
    out["linear_vs_seasonal_naive"] = {
        "mae_diff_mw": round(cmp["diff"], 1), "ci95_mw": [round(c, 1) for c in cmp["ci"]],
        "n_week_blocks": cmp["n_blocks"],
        "mae_reduction_pct": round(-100 * cmp["diff"] / metrics.mae(p["actual"], sn_aligned), 1)}

    p["err"] = p["forecast"] - p["actual"]
    daily = p.groupby("local_date").agg(mae=("err", lambda e: e.abs().mean()),
                                        bias=("err", "mean"))
    out["linear_worst_days"] = [
        {"date": d, "mae_mw": round(float(r["mae"]), 0), "bias_mw": round(float(r["bias"]), 0)}
        for d, r in daily.sort_values("mae", ascending=False).head(10).iterrows()]
    p["season"] = pd.to_datetime(p["local_date"]).dt.month.map(
        lambda m: "winter (Nov-Mar)" if m in (11, 12, 1, 2, 3) else "summer (Apr-Oct)")
    out["linear_by_season"] = {
        k: {"mae_mw": round(float(g["err"].abs().mean()), 1),
            "coverage_80_pct": round(100 * metrics.interval_coverage(g["actual"], g["q10"],
                                                                     g["q90"]), 1)}
        for k, g in p.groupby("season")}

    # Sensitivity check D3: Stockholm-only temperature.
    raw_temp = pd.read_csv(config.DATA_RAW / "smhi_air_temperature.csv.gz")
    sthlm = {"Stockholm-Observatoriekullen A": 1.0}
    data_s = build_dataset.with_alternative_temperature(data, raw_temp, sthlm)
    feat_s = features.build_features(data_s)
    res_s = backtest.run(feat_s, models.LinearModel(name="linear_stockholm_temp"), *win)
    backtest.assert_no_leakage(res_s)
    out["linear_stockholm_only_temperature"] = backtest.summarise(res_s.predictions, scale)

    # Temperature sensitivity on post-crisis data up to the end of validation.
    sens_win = ("2023-01-01", selection.VAL_END)
    out["sensitivity_weighted_temp"] = sens.daily_sensitivity(feat, *sens_win)
    out["sensitivity_stockholm_temp"] = sens.daily_sensitivity(feat_s, *sens_win)
    out["sensitivity_pre_crisis_2019_2021"] = sens.daily_sensitivity(
        feat, "2019-01-08", "2021-12-31")
    out["hourly_slopes_weighted_temp"] = sens.hourly_slopes(feat, *sens_win)

    pred_dir = config.REPORTS / "predictions"
    p.drop(columns=["timestamp_key", "err", "season"]).to_csv(
        pred_dir / "validation_linear.csv.gz", index=False)
    _write_json(config.REPORTS / "regression_validation.json", out)
    print(json.dumps({k: v for k, v in out.items() if k != "hourly_slopes_weighted_temp"},
                     indent=2, default=str)[:6000])


def _christmas_mask(p):
    import pandas as pd

    md = pd.to_datetime(p["local_date"]).dt.strftime("%m%d")
    return md.between("1222", "1231") | md.between("0101", "0107")


def _season(p):
    import pandas as pd

    m = pd.to_datetime(p["local_date"]).dt.month
    return m.map(lambda x: "winter (Nov-Mar)" if x in (11, 12, 1, 2, 3) else "summer (Apr-Oct)")


def _extended_summary(p, scale) -> dict:
    from se3load import backtest, metrics

    out = backtest.summarise(p, scale)
    out["pinball_q10"] = round(metrics.pinball_loss(p["actual"], p["q10"], 0.1), 1)
    out["pinball_q90"] = round(metrics.pinball_loss(p["actual"], p["q90"], 0.9), 1)
    out["mean_interval_width_mw"] = round(float((p["q90"] - p["q10"]).mean()), 1)
    out["christmas_days"] = backtest.summarise(p[_christmas_mask(p)], scale)
    out["by_season"] = {k: {"mae_mw": round(float((g["forecast"] - g["actual"]).abs().mean()), 1),
                            "coverage_80_pct": round(100 * metrics.interval_coverage(
                                g["actual"], g["q10"], g["q90"]), 1)}
                        for k, g in p.groupby(_season(p))}
    return out


def _paired(p_a, p_b, seed) -> dict:
    import pandas as pd

    from se3load import metrics

    m = p_a.merge(p_b[["timestamp_utc", "forecast"]], on="timestamp_utc",
                  suffixes=("_a", "_b"), validate="one_to_one")
    weeks = pd.to_datetime(m["local_date"]).dt.strftime("%G-%V").to_numpy()
    r = metrics.block_bootstrap_mae_diff(m["actual"], m["forecast_a"], m["forecast_b"],
                                         weeks, seed=seed)
    return {"mae_diff_mw": round(r["diff"], 1), "ci95_mw": [round(c, 1) for c in r["ci"]]}


def gbm_stage() -> None:
    import time

    import pandas as pd

    from se3load import backtest, features, models, selection

    data = _processed()
    feat = features.build_features(data)
    scale = backtest.mase_scale_from_history(data, selection.VAL_START)
    win = (selection.VAL_START, selection.VAL_END)
    out: dict = {"window": list(win), "mase_scale_mw": round(scale, 1)}
    preds = {}

    # D17: calendar variants, scored with the linear model on the validation year.
    out["calendar_variants_linear"] = {}
    for cal in ("v1", "v2a", "v2b"):
        r = backtest.run(feat, models.LinearModel(name=f"linear_{cal}", calendar=cal), *win)
        backtest.assert_no_leakage(r)
        preds[f"linear_{cal}"] = r.predictions
        out["calendar_variants_linear"][cal] = _extended_summary(r.predictions, scale)
    chosen = min(out["calendar_variants_linear"],
                 key=lambda c: out["calendar_variants_linear"][c]["mae_mw"])
    out["calendar_chosen"] = chosen
    out["calendar_chosen_vs_v1"] = _paired(preds[f"linear_{chosen}"], preds["linear_v1"],
                                           config.SEED)

    # Gradient boosting with v1 (as pre-registered) and with the chosen calendar.
    out["gbm"] = {}
    for cal in sorted({"v1", chosen}):
        t0 = time.time()
        r = backtest.run(feat, models.GBMQuantileModel(name=f"gbm_{cal}", calendar=cal,
                                                       seed=config.SEED), *win)
        backtest.assert_no_leakage(r)
        preds[f"gbm_{cal}"] = r.predictions
        out["gbm"][cal] = _extended_summary(r.predictions, scale)
        out["gbm"][cal]["runtime_s"] = round(time.time() - t0, 0)

    # Equal-weight average of linear and GBM (forecast and quantiles), same calendar.
    lin, gbm = preds[f"linear_{chosen}"], preds[f"gbm_{chosen}"]
    ens = lin.copy()
    ens["model"] = f"average_{chosen}"
    for c in ("forecast", "q10", "q90"):
        ens[c] = (lin[c].to_numpy() + gbm[c].to_numpy()) / 2
    preds[f"average_{chosen}"] = ens
    out["average"] = _extended_summary(ens, scale)

    out["paired_vs_linear"] = {
        "gbm": _paired(gbm, lin, config.SEED),
        "average": _paired(ens, lin, config.SEED)}

    pd.concat(preds.values(), ignore_index=True).to_csv(
        config.REPORTS / "predictions" / "validation_m7.csv.gz", index=False)
    _write_json(config.REPORTS / "gbm_validation.json", out)
    print(json.dumps(out, indent=2, default=str)[:8000])


def evaluate_stage() -> None:
    import pandas as pd

    from se3load import backtest, evaluate, features, figures, metrics, models

    data = _processed()
    feat = features.build_features(data)
    start, end = config.BACKTEST_START, config.BACKTEST_END
    scale = backtest.mase_scale_from_history(data, start)
    noisy = evaluate.noisy_feature_sets(data, start, n_reps=5, sd=1.5, rho=0.9,
                                        seed=config.SEED)
    out: dict = {"window": [start, end], "mase_scale_mw": round(scale, 1),
                 "preregistration": "docs/decisions.md D19", "models": {}}
    runs = {}
    for m in backtest.baselines():
        runs[m.name] = backtest.run(feat, m, start, end)
    for m in (models.LinearModel(name="linear", calendar="v2b"),
              models.GBMQuantileModel(name="gbm_quantile", calendar="v2b", seed=config.SEED)):
        r = backtest.run(feat, m, start, end, extra_predict=noisy)
        backtest.assert_no_leakage(r)
        runs[m.name] = r
        out.setdefault("fits", {})[m.name] = r.fits

    def observed(name):
        p = runs[name].predictions
        return p[p["variant"] == "observed"].drop(columns="variant") if "variant" in p else p

    preds = {n: observed(n) for n in runs}
    lin, gbm = preds["linear"], preds["gbm_quantile"]
    assert (lin["timestamp_utc"].to_numpy() == gbm["timestamp_utc"].to_numpy()).all()
    avg = lin.copy()
    avg["model"] = "average"
    for c in ("forecast", "q10", "q90"):
        avg[c] = (lin[c].to_numpy() + gbm[c].to_numpy()) / 2
    preds["average"] = avg

    for name, p in preds.items():
        s_ = backtest.summarise(p, scale)
        if p["q10"].notna().all() if "q10" in p else False:
            s_["intervals"] = evaluate.coverage_with_day_ci(p)
        out["models"][name] = s_

    weeks = lambda p: pd.to_datetime(p["local_date"]).dt.strftime("%G-%V").to_numpy()  # noqa: E731
    def boot(a, b):
        r = metrics.block_bootstrap_mae_diff(a["actual"], a["forecast"], b["forecast"],
                                             weeks(a), seed=config.SEED)
        return {"mae_diff_mw": round(r["diff"], 1), "ci95_mw": [round(c, 1) for c in r["ci"]],
                "pct_vs_b": round(100 * r["diff"] / metrics.mae(b["actual"], b["forecast"]), 1)}
    def dm(a, b):
        r = metrics.diebold_mariano(evaluate.daily_abs_error(a), evaluate.daily_abs_error(b))
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}
    sn = preds["seasonal_naive_d7"]
    out["primary_average_vs_seasonal_naive"] = {**boot(avg, sn), "dm": dm(avg, sn)}
    out["secondary_average_vs_linear"] = {**boot(avg, lin), "dm": dm(avg, lin)}
    out["average_vs_gbm"] = {**boot(avg, gbm), "dm": dm(avg, gbm)}
    out["linear_vs_gbm"] = {**boot(lin, gbm), "dm": dm(lin, gbm)}
    mae_ci = {}
    for name, p in preds.items():
        r = metrics.block_bootstrap_mae_diff(p["actual"], p["forecast"], p["actual"], weeks(p),
                                             seed=config.SEED)
        mae_ci[name] = [round(c, 1) for c in r["ci"]]
    out["mae_ci95_by_model"] = mae_ci

    allp = evaluate.add_context(pd.concat(preds.values(), ignore_index=True), feat, data)
    out["segments"] = evaluate.segment_table(allp)

    # Simulated weather-forecast error (D19), linear + GBM refits unchanged.
    sim = []
    for v in noisy:
        lv = runs["linear"].predictions.query("variant == @v")
        gv = runs["gbm_quantile"].predictions.query("variant == @v")
        av = lv.copy()
        for c in ("forecast", "q10", "q90"):
            av[c] = (lv[c].to_numpy() + gv[c].to_numpy()) / 2
        sim.append({"variant": v, "average_mae_mw": round(metrics.mae(av["actual"], av["forecast"]), 1),
                    "linear_mae_mw": round(metrics.mae(lv["actual"], lv["forecast"]), 1),
                    "gbm_mae_mw": round(metrics.mae(gv["actual"], gv["forecast"]), 1),
                    "average_coverage_80_pct": round(100 * metrics.interval_coverage(
                        av["actual"], av["q10"], av["q90"]), 1)})
    sim_df = pd.DataFrame(sim)
    out["weather_noise_simulation"] = {
        "setup": "AR(1) hourly temperature error, marginal SD 1.5 degC, rho 0.9, 5 replicates; "
                 "SIMULATED, applied only to prediction inputs from the backtest start",
        "replicates": sim,
        "average_mae_mean_mw": round(float(sim_df["average_mae_mw"].mean()), 1),
        "average_mae_range_mw": [float(sim_df["average_mae_mw"].min()),
                                 float(sim_df["average_mae_mw"].max())],
        "increase_vs_observed_mw": round(float(sim_df["average_mae_mw"].mean())
                                         - out["models"]["average"]["mae_mw"], 1)}

    # Figures
    labels = {"average": "Average (linear + GBM)", "linear": "Linear regression",
              "gbm_quantile": "Gradient boosting", "seasonal_naive_d7": "Same hour last week",
              "naive_d2": "Same hour two days before"}
    figures.mae_by_model(
        [{"label": labels[n], "mae": out["models"][n]["mae_mw"], "ci": mae_ci[n]} for n in preds],
        config.FIGURES / "backtest_mae_by_model.png",
        f"Model average: {-out['primary_average_vs_seasonal_naive']['pct_vs_b']:.0f} % lower "
        "hourly error than 'same hour last week'",
        f"Held-out backtest, SE3, {pd.Timestamp(start):%b %Y}–{pd.Timestamp(end):%b %Y}, "
        "one forecast per day issued 12:00 the day before")
    temp_daily = feat.groupby("local_date")["temp"].mean()
    temp_daily = temp_daily[(temp_daily.index >= start) & (temp_daily.index <= end)]
    coldest = pd.Timestamp(temp_daily.idxmin())
    wk_start = coldest - pd.Timedelta(days=3)
    wk = avg[(pd.to_datetime(avg["local_date"]) >= wk_start)
             & (pd.to_datetime(avg["local_date"]) < wk_start + pd.Timedelta(days=7))]
    wk_mae = metrics.mae(wk["actual"], wk["forecast"])
    out["hero_week"] = {"start": str(wk_start.date()), "coldest_day": str(coldest.date()),
                        "coldest_daily_mean_temp_c": round(float(temp_daily.min()), 1),
                        "average_mae_mw": round(wk_mae, 1)}
    figures.hero_week(
        wk, config.FIGURES / "hero_cold_week.png",
        f"Day-ahead forecast vs actual in the coldest week of the backtest",
        f"SE3, average model, week from {wk_start.date()} (coldest day {coldest.date()}, "
        f"{temp_daily.min():.1f} °C daily mean); MAE this week {wk_mae:.0f} MW")

    pd.concat(preds.values(), ignore_index=True).to_csv(
        config.REPORTS / "predictions" / "backtest_all_models.csv.gz", index=False)
    _write_json(config.REPORTS / "backtest_results.json", out)
    print(json.dumps({k: v for k, v in out.items() if k not in ("fits", "segments")},
                     indent=2, default=str)[:9000])


STAGES = {"probe": probe, "fetch": fetch, "reconcile": reconcile, "quality": quality_stage,
          "explore": explore_stage, "select": select_stage, "baselines": baselines_stage,
          "regression": regression_stage, "gbm": gbm_stage,
          "evaluate": evaluate_stage}


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
