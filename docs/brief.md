# Analysis brief

**Question.** How accurately can next-day hourly electricity consumption in bidding
zone SE3 be forecast at bid time, and how much does a temperature-and-calendar model
improve on a seasonal-naive rule?

**Who decides what.** A balance-responsible retailer's trading/forecasting team
decides which forecast to bid in the day-ahead auction and how much uncertainty
margin to plan for. Imbalance volume (|actual − forecast| in MWh) is what costs money.

**Metric, exactly.**
- Unit: hourly consumption in MWh/h (= average MW) for MBA SE3, as settled by eSett.
- Forecast: issued once per day at 12:00 CET (11:00 UTC in winter) for the 24 hours of
  the next calendar day (Europe/Stockholm; 23 or 25 hours on DST days).
- Primary: MAE in MW over the backtest; MASE (scaled by in-sample seasonal-naive MAE,
  season = 168 h); MAPE as a secondary, familiar number.
- Uncertainty: 80 % prediction interval coverage; bootstrap/block CI on MAE differences.
- Population: all hours 2025-11-01 to 2026-10-31 (held out). Training: from 2019-01-01.

**Done means.** Backtest metrics for baseline and models on identical hours, with
uncertainty, a segment breakdown and a clear recommendation.

**Results that would NOT answer the question.**
- In-sample fit or a single random train/test split (leaks the future).
- Using information not available at 12:00 the day before (e.g. same-day load lags).
- Accuracy only on averages, without the cold-peak hours.

**Must not change silently.** Bidding zone, forecast issue time and horizon, backtest
window, the definition of a valid hour, temperature weights.

**Sub-questions and sources.**
1. What does the data look like and is it complete? – eSett, SMHI (exist; verified by probe).
2. How does load depend on temperature, hour, weekday and holidays? – eSett + SMHI + calendar.
3. How good is seasonal naive? – eSett.
4. How much better are regression and gradient boosting? – all sources.
5. How much is lost with realistic weather-forecast error? – simulated noise on SMHI
   observations (clearly labelled as a simulation; historical forecasts are not
   freely available as open data).
