# Decisions and assumptions log

Each entry: what, why, alternatives, expected impact. Changes made *after* seeing
results are logged in a separate section at the bottom.

## Before looking at results

### D1. Bidding zone SE3 only
- **Why:** SE3 holds Stockholm and Göteborg and about two thirds of Swedish consumption (to be confirmed in M3); it
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

## After looking at results

_None yet._
