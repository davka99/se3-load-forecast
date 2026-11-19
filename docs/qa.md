# QA notes

## Reproducibility rerun

All offline stages were re-run from the committed raw files in one GitHub Actions job
(`quality explore select baselines regression gbm evaluate`, Python 3.12, pinned
requirements, fixed seed). The regenerated `reports/*.json` and the processed dataset
were compared with the versions produced stage by stage during development.

RERUN_RESULT

`scripts/check_readme.py` (also run as a test) checks that the 31 key numbers quoted in
the README match `reports/backtest_results.json` and `reports/regression_validation.json`.

## Three objections a skeptical reviewer would raise

**1. "The model is given the actual temperature, so the backtest is too optimistic."**
True, and it is the largest caveat. The setup is stated up front (D4). The backtest also
re-runs the same fitted models with simulated day-ahead temperature error (AR(1),
SD 1.5 °C): MAE rises from 198 to ≈ 241 MW (+22 %), still ~58 % below the seasonal naive.
The simulation is labelled as such. Historical day-ahead forecasts are not openly
archived, and with them the realistic error could be measured directly. That is the
first thing to add before using the model in production.

**2. "You tuned things until the numbers looked good."**
The held-out year was opened once, after the full evaluation plan was written down (D19:
models, headline model, comparisons, segments, CI methods). Everything before that used a
separate validation year. The level-shift handling (D11) was chosen by a rule fixed in
advance. The only post-result change (calendar v2, D17) is logged as such, with its small
gain (−2.0 MW) and the note that it was first scored locally. Gradient-boosting
hyperparameters were never tuned. The headline model was also best on the backtest, but
it would have stayed the headline either way.

**3. "65 % better than a naive rule is easy; any model beats 'same hour last week'."**
Partly true: the naive rule is the bar a small retailer might actually use, not a straw
man. In this window it did better than its own in-sample history (MASE 0.90), so the
year was not unusually hard for it. The stronger evidence is the comparison between the
models: the average beats the linear model by 26.6 MW (CI 16.9–36.5) and gradient
boosting by 12.5 MW (CI 1.6–23.6). The project also reports where the model is weak:
daily peaks under-forecast by 130 MW, intervals that miss peaks, and a −55 MW bias as
demand rose in 2026.
