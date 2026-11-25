# Decisions and assumptions log

Each entry: what, why, alternatives, expected impact. Changes made *after* seeing
results are logged in a separate section at the bottom.

## Before looking at results

### D1. Bidding zone SE3 only
- **Why:** SE3 holds Stockholm and Göteborg and about 63 % of Swedish settled consumption (62.4–63.2 % in 2019–2025, measured in M3); it
  is the zone most Swedish retailers forecast.
- **Alternatives:** all of Sweden; all four zones. Kept as possible extensions.
- **Impact:** findings do not transfer directly to SE1/SE2 (industrial, flatter load).

### D2. Consumption from eSett Open Data
- **Why:** open, keyless, official settlement data per bidding zone.
- **Alternatives:** ENTSO-E Transparency (needs a personal API token, which a scheduled
  agent cannot create), Svenska kraftnät statistics (monthly files, less convenient).
- **Impact:** settled consumption may differ from "total load" (e.g. losses, pumping);
  reconciliation in M3 quantifies this.
- **Verified by probe (2026-11-19):** fields `timestamp`, `timestampUTC`, `mba`,
  `metered`, `profiled`, `flex`, `total`. `total` is **negative** (eSett sign convention
  for consumption) and published per **15-minute** period already in Jan 2024.
  Magnitude (≈ −3,100 per 15 min in a cold January week) only makes sense as MWh per
  period (≈ 12.4 GW average), not as MW (≈ 3.1 GW, far below Stockholm alone).
  → `ESETT_VALUE_KIND = "energy"`: hourly MWh = sum of the periods; sign flipped
  explicitly in cleaning with wrong-sign values kept visible. Raw files keep the
  published sign. Unit is re-checked against annual totals in M3.
- **Full fetch (2026-11-19):** 159,318 rows, 2019-01-01 to 2026-10-31, no duplicate
  timestamps, every value negative, none missing. Resolution is **hourly until 2023 and
  15-minute from 2023** (the Nordic 15-minute imbalance settlement period). Summing to
  hours works for both because values are energy per period. Annual sums
  79–86 TWh (2019–2025), plausible for SE3; formal reconciliation in M3.

### D3. Population-weighted temperature from five SMHI stations
- **Stations pinned by id**, not name: the probe showed `Örebro A` does not exist
  (replaced by `Örebro Flygplats`, 95130) and two stations are called `Västerås`
  (96350 active, 28 closed). Each run verifies id, name and active flag and stops on
  any mismatch.
- **Västerås dropped (2026-11-19, before any modelling):** the full fetch showed that
  station 96350 reports only ~3 observations a day (5,671 values for 2019–2026 vs
  ~68,000 for the other stations). Keeping it would make the weighted temperature
  switch composition every few hours. Its weight (0.28 of 5.66, about 5 %) is
  dropped; Västmanland lies between Uppsala and Örebro, which are both included.
  Five stations remain.
- **Why:** load responds to where people live. Weights are approximate county
  populations (SCB 2024, rounded): Stockholm 2.45, Västra Götaland 1.75,
  Östergötland 0.47, Uppsala 0.40, Örebro 0.31 (millions).
- **Alternatives:** Stockholm only; equal weights; gridded reanalysis (ERA5, needs account).
- **Impact:** small; sensitivity check against Stockholm-only temperature planned in M6.
- **Missing stations in an hour:** weights renormalised over reporting stations and the
  covered weight share is stored, so thin hours are visible.

### D4. Forecast setup mimics the real bid
- One forecast per day issued at 12:00 CET for next calendar day (local time).
- Only information available at issue time: load up to the last fully settled hour
  is **not** assumed available (settlement data arrives later); lags ≥ 2 days are used.
- Observed temperature is used as the weather input = a perfect weather forecast. This
  flatters the model; M8 adds simulated forecast noise to quantify the gap.

### D5. Backtest window = last 12 months (2025-11-01 to 2026-10-31)
- **Why:** one full seasonal cycle including a winter, held out from all model tuning.
- **Alternatives:** multiple years of rolling origins (more robust, costlier).
- **Impact:** a single winter; an unusually mild or cold winter changes absolute errors.

### D6. Holidays implemented in code
- **Why:** avoids an extra dependency and makes the rules testable; Swedish holidays
  are fully rule-based (Easter computus + fixed/weekday rules).
- De facto holidays (Midsommarafton, Julafton, Nyårsafton) kept as a separate flag,
  plus bridge days (klämdagar).

### D7. Data and heavy computation run in GitHub Actions
- **Why:** the build environment has no access to the data APIs or PyPI. Actions output
  (data, test results) is committed back to the branch and counts as the test record.

### D8. Temperature cleaning rules (set before running them)
- Values outside −45…40 °C and isolated spikes (> 8 °C from the mean of both
  neighbouring hours, both present) → NaN, flagged. SMHI quality "Y" values are kept
  unless a rule fires: dropping them would remove real extremes.
- Weighted temperature renormalised over reporting stations; gaps with no station at
  all are interpolated only if ≤ 3 hours, longer gaps stay missing.
- **Result:** 0 rule hits, 0 hours imputed. The rules matter only as a safeguard for
  future data.

### D9. Load outliers are flagged, never removed
- Screen: robust z vs same hour-of-week ±1–2 weeks, scale = 1.4826 × rolling 30-day MAD,
  threshold |z| > 6. Stuck values: ≥ 3 identical consecutive hours.
- **Why flag only:** the extreme hours are real (holidays, cold snaps), and those are
  exactly the hours the business question asks about. Removing them would flatter the
  backtest.
- **Result:** 19 hours, 15 on holidays/eves; 4 on 2025-08-23 explained by an early cold
  spell. 0 stuck hours.

### D10. Reconciliation benchmark
- eSett four-zone total vs national use from Energimyndigheten/SCB (via Energiföretagen,
  *Energiåret 2024*), with and without transmission losses, for the years the table
  gives (2020, 2023, 2024).
- **Result:** within +1.3 % to +2.3 % of use excluding losses. Accepted as confirmation
  of unit, sign and coverage. The residual is not decomposed.

### D11. Post-2022 level shift → recency-weighted training (decided in M4 on the validation year)
- Finding (M3): at similar temperatures, working-day load in 2023–2026 is 2–5 % lower
  than in 2019–2021.
- Options: (a) train on 2023-01-01 onwards only; (b) train on all data with a
  period/regime indicator or a smooth trend term; (c) weight recent data more.
- Criterion, fixed now: choose on a **validation year (2024-11-01 to 2025-10-31)**
  that is separate from the backtest year, never on backtest results.
- **Procedure (M4):** linear model (Ridge, α = 1, hour×daytype profile, hour-specific
  heating slopes, flags, D-2/D-7 load lags), trained to 2024-10-31, one fit, scored on
  all 8,760 validation hours. Half-life for (c) fixed at 365 days *before* the run, not
  tuned. Primary = model with lags (that is what will be used); without lags reported
  for context. Paired weekly-block bootstrap (53 weeks, 2,000 draws, seed = config.SEED)
  for differences. Numbers below are from GitHub Actions run 44550627678
  (`reports/d11_training_window.json`).

  | Option | MAE (MW) with lags | Bias | MAE without lags | Bias |
  |---|---|---|---|---|
  | (d) all years, no adjustment | 244.3 | +49 | 277.1 | +133 |
  | (b) all years + regime terms | 240.3 | −69 | 268.3 | −103 |
  | (c) all years, recency-weighted | **230.4** | −15 | **234.3** | +1 |
  | (a) 2023 onwards only | 233.3 | −75 | 253.5 | −102 |

  (c) vs (d): −14.0 MW (95 % CI −22.0 to −5.7); vs (b): −9.9 (−17.2 to −2.7);
  vs (a): −2.9 (−10.3 to +4.5, not distinguishable).
- **Decision: (c) recency weighting, half-life 365 days**, for all models. It wins on the
  rule. It is clearly better than ignoring the shift, which over-forecasts by +133 MW
  without lags and confirms the M3 finding. It cannot be told apart from training on
  2023+ only, but it keeps the 2019–2022 cold winters in the data with less weight. That
  matters for the peak hours the business question asks about: 2023+ has few days
  below −5 °C.
- Load lags absorb most of the shift on their own (bias +49 vs +133 MW).

### D12. Load data availability at bid time
- Assumed known at 12:00 on D-1: zone load up to the end of local day D-2. eSett's
  open data is published with a delay, so a real retailer would use its own metering
  and Svenska kraftnät's operational data; D-2 is a conservative stand-in.
- Lags are built on **local calendar dates** (same local clock hour on D-2 and D-7,
  plus daily means), not fixed UTC offsets. A 48 h UTC lag reads from D-1 for the
  last hour of the day after the autumn DST change; a test documents this. Autumn
  duplicate hour → mean of both; spring missing 02:00 → 03:00 of the same day.
- Leakage test: load encoded as its own hour index; every lag value used across a
  DST window is checked to come from before local midnight starting D-1.

### D13. Feature set (fixed before the backtest)
- Calendar: local hour, weekday, day type (`sunday_or_holiday` covers public and de facto
  holidays, as M3 showed they behave like Sundays; plus saturday, monday, friday, bridge,
  midweek), industrial summer break (ISO weeks 28–31), Christmas period (24 Dec–6 Jan),
  yearly Fourier terms (2 harmonics).
- Temperature: temperature, heating degrees below 15 °C (the M3 scatter flattens around
  15 °C), cooling degrees above 20 °C, exponentially smoothed temperature (half-life
  12 h and 48 h, for building inertia), daily mean temperature.
- MSTL of daily load (M4): yearly component strength 0.89, weekly 0.30, trend 0.09.
  Weekend days sit ~0.9 GW below weekdays. Yearly component range −3.0 to +4.8 GW.

### D14. Backtest design (M5, fixed before any model is backtested)
- **Rolling origin, one forecast per target local day**, issued 12:00 on D-1. A model
  may be fitted only on rows with local_date ≤ D-2. Every fit logs its cutoff, and
  `assert_no_leakage` re-checks it after each run.
- **Refit every 28 days** and reuse the latest fit in between, as a production forecast
  would. Alternatives: refit daily (realistic for a linear model but 365 fits per model)
  or once (understates what retraining gives). 28 days keeps the gradient-boosting
  backtest within an Actions run.
- **Recency weights** (D11) recomputed at each refit relative to its cutoff.
- **MASE scale:** in-sample MAE of the 168 h seasonal naive on all hourly history
  before the evaluation window (2019-01-01 → day before). MASE < 1 = better than the
  seasonal naive was in-sample.
- **Baselines:** `seasonal_naive_d7` (same local hour one week earlier) and `naive_d2`
  (same local hour on D-2, the latest day known at bid time).
- **Windows:** M5–M7 develop on the validation year (2024-11-01 → 2025-10-31).
  The backtest year (2025-11-01 → 2026-10-31) is run **once**, in M8, for all models.

### D15. Linear forecast model and its intervals (M6)
- Ridge (α = 1) on the D13 design with D-2/D-7 lags, recency weights (D11), refits
  every 28 days (D14). Ridge rather than plain OLS because the hour×daytype dummies
  and hour-specific heating slopes are partly collinear; α = 1 is negligible next to
  the sum of squares and was not tuned.
- **80 % intervals by split conformal:** at each refit the model is fitted without its
  last 90 days, and the 10th/90th percentiles of its errors on those 90 unseen days
  are added to the full-data forecast. This was chosen over in-sample residual
  quantiles, which are too narrow by construction. Known weakness, set before
  results: one pooled residual distribution for all hours and seasons.

### D16. Temperature sensitivity (business secondary question)
- Daily OLS: daily mean load ~ HDD(15 °C) + CDD(20 °C) + day type + month + summer-break
  + Christmas flags, **Newey-West (HAC) SEs with 14 lags**. Month dummies mean the slope
  comes from weather variation *within* a month, so daylight and seasonal habits are
  not counted as temperature effect.
- No load lags: they would absorb the temperature effect.
- Main window 2023-01-01 → 2025-10-31: the current regime (D11), and the backtest year
  stays closed. Pre-crisis 2019–2021 is estimated as a comparison.
- Hourly variant: one HDD slope per local hour, HAC with 168 lags.

### D18. Gradient-boosting quantile model (M7, fixed before its first run)
- scikit-learn `HistGradientBoostingRegressor`, quantile loss, one model each for the
  10 %, 50 % and 90 % quantiles. The median is the point forecast because it minimises
  absolute error, the business metric.
- Hyperparameters fixed up front, not tuned: 400 iterations, learning rate 0.05,
  31 leaves, min 40 samples per leaf, L2 = 1, no early stopping (deterministic),
  seed = config.SEED. They are conventional mid-range values; tuning on the validation
  year would cost a run and risk overfitting one winter.
- Features: raw calendar and temperature columns, load lags, day type as a categorical.
  **No trend term**, because trees cannot extrapolate it; the level shift is handled by
  recency weights (D11) and lags.
- Crossing quantiles are sorted per row (monotone rearrangement).
- Also evaluated: an **equal-weight average** of linear and GBM (forecast and quantile
  bounds). It was chosen as a candidate before results because averaging different
  model types is a cheap, well-known way to cut forecast error.

## After looking at results

### R1. M6 results on the validation year (2024-11-01 → 2025-10-31)
Source: `reports/regression_validation.json`, Actions run 44552103105.
- Linear model: MAE 228.7 MW, RMSE 289.0, MAPE 2.55 %, MASE 0.365, bias −13.5 MW,
  80 % interval coverage 81.5 % (winter 77.6 %, summer 84.3 %; the pooled-residual
  weakness of D15 shows up as expected).
- vs seasonal naive: −415.6 MW MAE (95 % weekly-block CI −520.6 to −318.2), a 64.5 %
  reduction.
- **D3 check:** with Stockholm-only temperature the same model gives MAE 256.7 MW, so the
  population-weighted temperature is better by 28 MW. D3 is kept. The check was run as
  pre-registered, not chosen after the fact.
- **Temperature sensitivity (2023–Oct 2025):** 202.4 MW per °C of daily mean below
  15 °C, 95 % CI 188.4–216.4 (≈ 4.9 GWh per day per °C). The HAC SE (7.1) is 2.3× the
  naive OLS SE (3.1); ignoring autocorrelation would have overstated the precision.
  Pre-crisis 2019–2021: 227.0 (214.3–239.7). The CIs do not overlap, so the heating
  response is ~11 % weaker since 2023, which fits M3's finding that the post-2022
  gap is largest in cold weather. Stockholm-only temperature: 189.9 (173.3–206.6),
  slightly attenuated, as expected when the temperature measure is noisier.
- Hourly slopes peak at 17:00 (231 MW/°C, CI 220–241) and 07:00 (219, 208–229), and
  are lowest at 23:00 (172, 161–184). Cold adds most at the morning and evening peaks.
- **Worst validation days** are the Christmas/New Year period (31 Dec −822 MW bias,
  6 Jan −737, 3–4 Jan −588/−651; negative = under-forecast) and 22 Dec (+593). The
  flat `is_christmas_period` flag plus the holiday day type push these days too low.
  Also 2025-05-07 (+569, unexplained) and Midsommardagen 2025-06-21 (+542).
  → **Diagnostic-driven candidate for M7** (allowed, since this is the validation year):
  split the Christmas period into holiday days vs working days in between. Must be
  logged as a post-result change and re-scored on validation only.


### D17. Calendar v2 – a diagnostic-driven change (post-result, validation year only)
- **Trigger:** M6 worst days were the Christmas/New Year period (R1).
- **Variants**, each defined from the calendar rather than fitted to errors:
  - v2a: replace the flat `is_christmas_period` flag (24 Dec–6 Jan) with
    `is_christmas_workday` (ordinary Mon–Fri working days 27 Dec–5 Jan that are not
    holidays, eves or bridge days).
  - v2b: v2a + a separate `holiday_eve` day type for de facto holidays
    (Midsommarafton, Julafton, Nyårsafton), instead of treating them as Sundays.
- **Honesty note:** the variants were first scored in a local run (v1 228.7, v2a 227.8,
  v2b 226.7 MW validation MAE for the linear model) and then re-run in Actions.
- **Rule:** the variant with the lowest validation MAE (linear model) is used for all
  models in M8. A paired weekly-block bootstrap vs v1 is reported. Only these three
  variants were tried, so the multiple-comparison risk is small but not zero.
- The backtest year was not used.

- **Result (Actions run 44556238341):** linear validation MAE v1 228.7, v2a 227.8,
  **v2b 226.7 MW → v2b chosen**. v2b vs v1: −2.0 MW (95 % CI −3.9 to −0.5). Small
  overall, as expected for a handful of days. On 22 Dec–7 Jan the linear MAE falls from
  395.7 to 368.3 MW.

### R2. M7 results on the validation year (`reports/gbm_validation.json`)
| Model (calendar v2b) | MAE | RMSE | MAPE | MASE | 80 % coverage (winter / summer) | Mean width |
|---|---|---|---|---|---|---|
| Linear | 226.7 | 286.4 | 2.53 % | 0.361 | 81.4 % (77.6 / 84.0) | 753 MW |
| GBM quantile | 227.5 | 295.4 | 2.47 % | 0.363 | **68.0 %** (64.3 / 70.6) | 574 MW |
| **Average linear + GBM** | **207.3** | **267.7** | **2.26 %** | **0.331** | 80.5 % (74.2 / 84.9) | 664 MW |

- GBM vs linear: +0.9 MW (95 % CI −12.6 to +13.9), a tie overall. GBM is better in
  summer (182 vs 203 MW) and worse in winter (292 vs 261). Trees cannot extrapolate
  beyond the temperature and load ranges seen in training, while the linear heating
  slope can.
- Average vs linear: **−19.3 MW (95 % CI −27.3 to −11.5)**. The two models make
  different errors, so averaging helps.
- **GBM quantiles undercover (68 % for a nominal 80 %).** Quantile loss is fitted
  in-sample, so the tails come out too narrow. The linear split-conformal intervals
  are close to nominal. In the average, the two widths offset each other (80.5 %),
  but winter is still too narrow (74.2 %).
- With calendar v1, GBM scored 224.8 MW, slightly better than with v2b. The calendar
  was chosen by the linear-model rule (D17) and is not changed per model, to avoid
  picking the best of many combinations.

### D19. Pre-registered for M8 (written before the backtest year is opened)
- **Models:** seasonal naive (D-7), naive (D-2), linear v2b, GBM v2b,
  average(linear, GBM) v2b. All are run once on 2025-11-01 → 2026-10-31 with the D14
  framework and refitted every 28 days on data up to D-2. Nothing else changes.
- **Headline model = the average**, chosen on the validation year. It is reported in
  the README whatever its backtest rank; if another model wins on the backtest, that
  is reported as a finding, not used to swap the headline.
- **Primary comparison:** average vs seasonal naive, MAE with a weekly-block bootstrap
  CI. Secondary: average vs linear, Diebold–Mariano on daily mean absolute error.
- **Segments:** winter/summer, cold hours (temperature < −5 °C), daily peak hour,
  holidays/eves, weekday/weekend, and Aug–Oct 2026, where the temperature comes from
  SMHI's less-controlled latest-months data.
- **Intervals:** hourly 80 % coverage, with a Wilson CI that uses the number of days
  as the effective sample size (successes = coverage × days). Hours within a day are
  strongly autocorrelated, so counting hours as independent would make the CI far too
  narrow. Pinball loss is reported for q10 and q90.
- **Realism check:** add simulated temperature-forecast error (Gaussian, SD 1.5 °C
  day-ahead, AR(1) across hours with ρ = 0.9; labelled as simulation) to the inputs
  for D and re-score the headline model.

### R3. M8 – held-out backtest, run once as pre-registered in D19
Source: `reports/backtest_results.json`, Actions run 44565082464. Window: 2025-11-01 →
2026-10-31, 8,760 hours, 365 forecasts, refits every 28 days on data up to D-2.
MASE scale 630.0 MW.

| Model | MAE (95 % CI) | RMSE | MAPE | MASE | Bias | 80 % coverage (Wilson CI, n = days) |
|---|---|---|---|---|---|---|
| Same hour last week | 567.9 (476.8–676.0) | 799.1 | 5.95 % | 0.901 | −1 | – |
| Same hour D-2 | 663.4 (609.4–720.7) | 876.0 | 7.26 % | 1.053 | +3 | – |
| Linear (v2b) | 224.8 (207.9–243.0) | 286.2 | 2.52 % | 0.357 | −64 | 77.5 % (73.0–81.5) |
| GBM quantile (v2b) | 210.7 (194.7–228.1) | 271.8 | 2.30 % | 0.334 | −47 | 67.8 % (63.0–72.5) |
| **Average (headline)** | **198.2 (184.8–213.3)** | **252.8** | **2.19 %** | **0.315** | −55 | 77.9 % (73.3–81.8) |

- **Primary:** average vs same hour last week −369.8 MW (95 % CI −477.6 to −279.0),
  **−65.1 %**. Diebold–Mariano on daily MAE: t = −6.71, p < 0.001.
- **Secondary:** average vs linear −26.6 MW (CI −36.5 to −16.9), DM t = −5.06, p < 0.001.
  Average vs GBM −12.5 (CI −23.6 to −1.6), p = 0.027. Linear vs GBM +14.1
  (CI −4.4 to +33.3), p = 0.17, so those two cannot be ranked. The headline model is
  also the best model on the backtest; the ranking matches validation.
- **Segments (average model):** winter 210.1 MW / summer 189.7; weekdays 196.1 /
  weekends 196.9; holidays and eves (17 days) 246.4; **cold hours < −5 °C (417 h, 30
  days) 242.6, bias −41**; **daily peak hour 233.6 with bias −130 and 68.5 %
  coverage**. Peaks are systematically under-forecast and the interval misses them
  more often.
- **Cold hours:** linear bias −234 MW (under-forecast), GBM +151 MW (over-forecast).
  The two models err in opposite directions when it is cold, which is why their
  average works (−41).
- **Bias over the year:** −55 MW overall, worse in May/June/Sep/Oct 2026 (−115 to −178
  MW per month), positive in Jul (+93) and Dec/Feb. Load in Jun–Oct 2026 was 70–240 MW
  above the same month in 2025 at similar or warmer temperatures. Demand rose in
  2026, and the recency-weighted level (half-life 365 days) adapts slowly.
- **Aug–Oct 2026** (temperature from SMHI's less-controlled latest-months data):
  MAE 184.6, bias −125. Accuracy is not worse than elsewhere, but the bias above
  shows up here too.
- **Intervals:** average 77.9 % (Wilson CI on days 73.3–81.8, includes 80 %); linear
  77.5 %; GBM 67.8 %, too narrow, as on validation.
- **Simulated weather-forecast error** (AR(1), SD 1.5 °C, ρ 0.9, 5 replicates;
  SIMULATION): average MAE 241.4 MW (range 232.9–251.8), **+43 MW (+22 %)** vs the
  perfect-forecast assumption; coverage drops to 68–72 %. Linear suffers more
  (263–283) than GBM (239–255). The realistic accuracy is therefore ≈ 240 MW / ≈ 2.7 %
  MAPE, still ~58 % below the seasonal naive.
- **Hero week** (coldest week of the backtest, 2026-02-11 → 17, coldest day 14 Feb at
  −9.7 °C daily mean): average MAE 290.7 MW.
- In this window the seasonal naive was better than its in-sample history
  (MASE 0.90), so the 65 % reduction is not inflated by an unusually hard year for
  the baseline.

### D20. Post-backtest extension: real archived temperature forecasts
Written before the forecast data were downloaded. Added after R3, so it is evaluated on
the same held-out year and is **not** an untouched test; the README says so.
- **Why:** R3's realism check used *simulated* forecast error. The real error has a
  different structure (larger in cold snaps, a bias between a model grid point and a
  station), so the limitation "perfect weather forecast" deserves a measured answer.
- **Data:** Open-Meteo Previous Runs API (CC BY 4.0, from January 2024),
  `temperature_2m_previous_day1` and `_day2` at the five pinned SMHI stations
  (coordinates from SMHI's station list), 2025-09-01 → 2026-10-31. `best_match` model;
  ECMWF IFS 0.25° only if `best_match` has more than 2 % missing values.
- **Lead time:** the forecast for day D is issued at 12:00 on D-1 (lead 12–36 h).
  `previous_day1` uses runs made ~24 h before each hour, partly after the deadline
  (optimistic); `previous_day2` uses runs ~48 h before, all before the deadline
  (pessimistic). Both are reported; the operational error lies between them.
- **Bias correction:** forecast minus the mean error of the same UTC hour over the
  previous 28 days whose observations were known at issue (up to D-2). This is what a
  forecaster would do with a raw weather feed; raw and corrected are both reported.
- **Use:** the same models and refits as D19; forecast temperature replaces observed
  temperature only in the prediction inputs from the backtest start, exactly as the
  D19 simulation did. Training data are unchanged.
- **Reported:** MAE with weekly-block CI, bias, coverage, and the change vs the
  observed-temperature run and vs the seasonal naive.

### D21. Post-backtest extension: online interval recalibration and peak handling
Written before the calibration was run. Same status as D20 (not an untouched test).
- **Why:** R3 found GBM intervals too narrow and the daily peak hour under-forecast
  (−130 MW, 68.5 % coverage). R3's peak segment is the *actual* peak hour, which is
  only known afterwards, so a fix must be judged on what is known at issue time.
- **Interval:** conformalised quantile regression, online. For day D, score =
  max(q10 − y, y − q90) on the previous 90 days with known load (up to D-2), at least
  28 days; the 80 % quantile of past scores widens [q10, q90]. Separate adjustment for
  the *predicted* peak hour (highest forecast of the day) and the other hours.
- **Peak:** two margins learned the same way: (a) the median past residual at the
  predicted peak hour, added to that hour; (b) the median past gap between the day's
  highest actual and highest forecast load, added to the forecast daily maximum.
- **Evaluation:** before vs after on the same days (the first ~29 days have no history
  and are excluded from both), for all hours, the predicted peak hour, the actual
  peak hour and the daily maximum. Run on the observed-temperature average (as D19)
  and on the realistic input (day-2, bias-corrected forecast).
