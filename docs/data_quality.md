# Data quality report

All numbers come from `reports/data_quality_summary.json`, produced by
`python scripts/run_pipeline.py reconcile quality` (GitHub Actions run 44548231414,
Python 3.12, pinned requirements). The clean dataset is
`data/processed/se3_hourly.csv.gz`: one row per UTC hour, 68,664 rows,
2019-01-01 00:00 to 2026-10-31 23:00 UTC.

**Summary.** Both sources are complete and consistent. No consumption hour is
missing, duplicated or partial, and no value had to be changed apart from the sign
convention. The national eSett total matches official statistics to within about 2.5 %,
which also confirms the unit. Two findings shape the modelling: holiday mornings are
the most extreme hours in the series, and SE3 has used about 2–5 % less power at the
same temperature since 2023 than in 2019–2021.

## 1. Consumption (eSett, SE3)

| Check | Result |
|---|---|
| Granularity | One row per settlement period: **hourly until 2023-05-22 00:00 local**, 15-minute from then on (Nordic 15-min imbalance settlement) |
| Raw rows | 159,318 |
| Duplicate timestamps | 0 |
| Missing values | 0 |
| Sign | all 159,318 negative (eSett convention for consumption) → flipped |
| Hours after aggregation | 68,664 = every hour of the window |
| Missing hours | 0 |
| Incomplete hours (periods ≠ 1 before / 4 after the switch) | 0 |
| Range | 4,801 – 16,290 MW, mean 9,259 MW |
| Peak | 2021-02-10 08:00 local, 16,290 MW (cold spell) |
| Stuck values (≥ 3 identical hours) | 0 |

**Unit.** Values are energy per period (MWh). Hourly MWh = sum of the periods =
average MW over the hour. Confirmed by the reconciliation below: read as MW, the
national total would be four times too small after 2023.

### Outlier screen (flagged, not removed)

Each hour is compared with the median of the same hour of the week in the two weeks
before and after; the residual is scaled by a rolling 30-day robust spread
(1.4826 × MAD). |z| > 6 is flagged (`flag_load_outlier`).

- **19 hours flagged, 15 of them on holidays or holiday eves**: early mornings
  on Midsommarafton (every year), Sveriges nationaldag 2022 and 2024, and
  Kristi himmelsfärd 2022. The data is correct; the reference weeks are ordinary
  working days. This tells us the model needs holiday features, and the backtest
  should report holiday errors separately.
- **4 hours on 2025-08-23 (Saturday, 04–07 local)** are flagged as unusually *high*
  (z ≈ 6–6.7). This is a weather effect that the temperature-blind screen cannot
  see. 22–23 August were the coldest days of that late summer: daily mean 11.6–12.1 °C
  and night minimum 8 °C. The reference Saturdays (9, 16 and 30 August, 6 September)
  had daily means of 15.9–17.7 °C, and early August is still in the industrial summer
  break. The hours are kept and flagged; they fall before the backtest window.
  Lesson: a calendar-only outlier screen confuses weather with errors, so the
  model-based residual check in M6 will re-screen with temperature included.

## 2. Temperature (SMHI, 5 stations)

| Station (id) | Weight | Coverage | Quality "Y" | Rule hits |
|---|---|---|---|---|
| Stockholm-Observatoriekullen A (98230) | 2.45 | 99.97 % | 26 | 0 |
| Göteborg A (71420) | 1.75 | 98.73 % | 26 | 0 |
| Linköping-Malmslätt (85240) | 0.47 | 99.63 % | 4 | 0 |
| Uppsala Aut (97510) | 0.40 | 99.01 % | 3 | 0 |
| Örebro Flygplats (95130) | 0.31 | 99.22 % | 4 | 0 |

- Rules: values outside −45…40 °C, and isolated spikes more than 8 °C from the mean of
  both neighbours, are set to NaN. **0 hits.** A steady front passage, such as a 9 °C drop
  over 3 hours, does not trigger the spike rule (tested).
- SMHI quality code "Y" (suspect/unchecked) applies to 63 values. They are kept because
  none fails the range or spike rules.
- Weighted temperature: every hour has at least one station. **0 hours imputed,
  0 missing.** In 5 hours, less than 50 % of the weight reported (Stockholm and
  Göteborg both missing); those hours rely on the smaller stations.
- **Source boundary:** SMHI's quality-controlled archive ends 2026-08-01 06:00 UTC.
  Later hours (inside the backtest) come from `latest-months`, which is less
  controlled (`temp_from_latest` flag). The same range and spike rules pass. Backtest
  errors are reported for this sub-period separately.
- Västerås (96350) was excluded before cleaning (about 3 readings a day; D3).

## 3. Time handling

All processing is in UTC. Local time (Europe/Stockholm) is derived for calendar
features and for the forecast day. Local day lengths: 2,844 days of 24 h,
8 days of 25 h (autumn DST), and 9 days of 23 h (8 spring DST days plus the first
day, which starts at 01:00 local). The last day (2026-11-01) has 1 hour. Day-level
analysis uses only complete local days (≥ 23 h).

## 4. Reconciliation against official statistics

eSett consumption for all four zones, summed by local calendar year (`reconcile`
stage), compared with national electricity use from Energimyndigheten/SCB as
tabulated in Energiföretagen, *Energiåret 2024*:

| Year | eSett SE1+SE2+SE3+SE4 (TWh) | Official, incl. losses | Official, excl. transmission losses | eSett vs excl. losses |
|---|---|---|---|---|
| 2020 | 128.9 | 138.2 | 127.3 | +1.3 % |
| 2023 | 127.0 | 134.3 | 124.7 | +1.9 % |
| 2024 | 128.5 | 135.0 | 125.6 | +2.3 % |

eSett settled consumption matches national use **excluding grid losses to within
1–2.5 %**. It is 5–7 % below the figure including losses, which fits settled
consumption covering metering points rather than network losses. The remaining
+1–2 % is not decomposed; it could come from partly settled losses or definition
differences. That is precise enough to confirm unit, sign and coverage.
Energimyndigheten's own 2024 press release rounds the total to 136 TWh, a different
data vintage.

**SE3 share of national settled consumption: 62.4–63.2 % (2019–2025).** Decision log
D1 said "about two thirds"; it now uses the measured ~63 %.

## 5. Structural change: less consumption at the same temperature after 2022

Mean daily load on working days (holidays excluded), compared at similar
temperatures. 2022, the energy-crisis year, is left out:

| Daily mean temp | 2019–2021 | 2023–Oct 2026 | Difference | 95 % CI |
|---|---|---|---|---|
| ≤ 0 °C | 13,120 MW | 12,512 MW | −608 MW (−4.6 %) | −839 to −377 |
| 0–5 °C | 11,297 | 10,916 | −381 (−3.4 %) | −510 to −252 |
| 5–10 °C | 10,065 | 9,550 | −515 (−5.1 %) | −653 to −376 |
| 10–15 °C | 8,565 | 8,315 | −250 (−2.9 %) | −371 to −128 |
| > 15 °C | 7,627 | 7,454 | −173 (−2.3 %) | −268 to −78 |

This is descriptive, not causal. Prices, industrial activity, heat pumps and
population all changed between the periods. Within-bin temperature differences are
not adjusted for, and the CIs treat days as independent, so they are too narrow.
Still, the gap is consistent across bins and largest in heating weather. Annual
totals tell the same story: 2023 had more heating degree hours than 2019
(80.3k vs 76.7k) but 3.4 TWh less consumption.

**Consequence for modelling:** a model trained on 2019–2021 without accounting for
this would over-forecast the backtest year. Options are logged as D11 and decided in
M4/M6 before the backtest is run.

## 6. What was changed in the data

| Change | Rows affected | Flag |
|---|---|---|
| eSett sign flipped to positive | all | – (raw file keeps the published sign) |
| 15-min periods summed to hours | 2023-05-22 → end | `n_periods` |
| Temperature out-of-range / spike → NaN | 0 | `temp_rule` (in cleaning) |
| Short temperature gaps interpolated (≤ 3 h) | 0 | `temp_imputed` |
| Load outliers | 0 changed, 19 flagged | `flag_load_outlier`, `load_robust_z` |
