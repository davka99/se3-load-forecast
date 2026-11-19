# PLAN – SE3 day-ahead electricity consumption forecast

## Business question

A balance-responsible party (elhandelsbolag/balansansvarig) in bidding zone SE3 must
submit tomorrow's hourly consumption before the day-ahead auction closes (12:00 CET).
Every MWh of forecast error is settled as imbalance at eSett.

**How accurately can next-day hourly consumption in SE3 be forecast from calendar and
temperature, how much better is that than the seasonal-naive rule a small retailer
might use, and how does accuracy hold up on the hours that matter most (cold winter
peaks and holidays)?**

Secondary finding: how many MW does SE3 consumption rise per °C of cold, with a
confidence interval (useful for capacity and peak planning).

Decision supported: whether a temperature-and-calendar model is worth running instead
of a naive rule, and how wide the hourly uncertainty band should be when bidding.

## Data source and how it is fetched

| Source | What | Access |
|---|---|---|
| [eSett Open Data](https://api.opendata.esett.com) `/EXP15/Consumption` | Settled consumption for MBA SE3 (EIC `10Y1001A1001A46L`), hourly until Sept 2025 and 15-min after the MTU switch | Open REST API, no key; fetched in 31-day chunks |
| [SMHI Open Data Meteorological Observations](https://opendata.smhi.se/apidocs/metobs/) parameter 1 | Hourly air temperature at 5 SE3 stations, population-weighted | Open REST API, no key; `corrected-archive` CSV + `latest-months` JSON |
| Swedish holidays | Public + de facto holidays, bridge days | Computed in `src/se3load/calendar_se.py` |

Study window: 2019-01-01 to 2026-10-31 (UTC). Backtest (held out): 2025-11-01 to 2026-10-31.

The sandbox that builds this project cannot reach these APIs, so downloads and heavy
computation run in GitHub Actions (`.github/workflows/se3-load-forecast-pipeline.yml`
in the workspace repo, triggered by editing `pipeline_request.txt`; the standalone
copy in `.github/workflows/pipeline.yml` uses `workflow_dispatch`).

## Methods

- Data quality: completeness per hour, DST and the 15-min MTU switch, duplicates,
  outliers, reconciliation of annual totals against Svenska kraftnät / Energimyndigheten.
- Exploration: STL decomposition (daily, weekly, yearly), load–temperature curve.
- Baselines: seasonal naive (same hour last week), plus same hour yesterday.
- Model 1: linear regression with heating-degree terms, hour×daytype, holidays
  (statsmodels OLS with HAC standard errors → temperature sensitivity with CI).
- Model 2: gradient boosting (scikit-learn HistGradientBoosting) with quantile loss
  for 10/50/90 % forecasts.
- Evaluation: rolling-origin backtest with one forecast per day issued at 12:00 CET
  for the next calendar day, MAE, MAPE, MASE, 80 % interval coverage (Wilson CI on
  daily blocks), Diebold–Mariano test vs baseline, errors by segment.
- Realism check: observed temperature is a "perfect weather forecast"; quantify how
  much accuracy is lost when realistic temperature-forecast noise is added.

## Milestones (one per run)

- [x] M1. Scaffold: plan, brief, README skeleton, decisions log, pinned requirements,
      source parsers with offline tests, CI and pipeline workflows; source probe.
- [x] M2. Verify probe output (schema, unit, sign, station names), fix config, then
      fetch the full window via GitHub Actions.
- [x] M3. Data quality report (`docs/data_quality.md`) and clean hourly dataset
      `data/processed/se3_hourly.csv.gz` with documented, tested rules.
- [ ] M4. Exploration: STL decomposition and load–temperature figure; feature
      module (calendar, heating degrees, lags available at bid time) with tests;
      decide D11 (post-2022 level shift) on the validation year 2024-11 → 2025-10.
- [ ] M5. Rolling-origin backtest framework + seasonal-naive baselines with tests.
- [ ] M6. Regression model + temperature sensitivity with HAC confidence interval.
- [ ] M7. Gradient boosting quantile model and backtest.
- [ ] M8. Evaluation: metrics with uncertainty, DM test, segment errors,
      weather-forecast-noise sensitivity.
- [ ] M9. Final figures, README with real results, QA rerun from scratch,
      skeptic's three objections.

Max 15 build runs.

## Definition of Done

- [x] All data fetched by a script from public sources; raw files and manifest committed
- [x] `docs/data_quality.md` written; no silent cleaning
- [x] Annual totals reconciled against a published figure (difference explained)
- [ ] Baseline and models evaluated on the same held-out backtest
- [ ] Key metrics reported with uncertainty (CI or interval) and MASE vs baseline
- [ ] Segment check (winter peak, holidays, weekday/weekend) done
- [ ] `docs/decisions.md` covers every choice that affects results
- [ ] Tests pass locally (offline) and in GitHub Actions
- [ ] README follows the portfolio standard with real numbers and limitations
- [ ] Every number in README is reproducible by `run_pipeline.py`
