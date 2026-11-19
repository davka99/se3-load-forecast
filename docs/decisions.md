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
- **To verify by probe (M2):** field names, unit (MWh per period vs MW), sign,
  resolution before/after the 15-minute MTU switch (Oct 2025).

### D3. Population-weighted temperature from six SMHI stations
- **Why:** load responds to where people live. Weights are approximate county
  populations (SCB 2024, rounded): Stockholm 2.45, Västra Götaland 1.75,
  Östergötland 0.47, Uppsala 0.40, Örebro 0.31, Västmanland 0.28 (millions).
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
