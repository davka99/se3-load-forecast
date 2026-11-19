"""Forecast models that plug into backtest.run."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from . import features


@dataclass
class LinearModel:
    """Ridge regression on the linear design (D13) with split-conformal intervals.

    Intervals: at each refit the model is first fitted on the training data minus the
    last `calib_days`, and its errors on those held-out days give the 10th/90th
    percentile of residuals. The model is then refitted on all training data and the
    residual quantiles are added to its point forecast. Residuals come from data the
    calibration fit has not seen, so the intervals are not narrowed by in-sample fit.
    """
    name: str = "linear"
    alpha: float = 1.0
    use_lags: bool = True
    calib_days: int = 90
    quantiles: tuple[float, float] = (0.1, 0.9)
    calendar: str = "v1"
    needs_fit: bool = True
    _model: Ridge | None = field(default=None, repr=False)
    _columns: list[str] = field(default_factory=list, repr=False)
    _resid_q: tuple[float, float] = (0.0, 0.0)

    def _design(self, feat: pd.DataFrame) -> pd.DataFrame:
        X = features.linear_design(feat, use_lags=self.use_lags, calendar=self.calendar)
        if self._columns:
            X = X.reindex(columns=self._columns, fill_value=0.0)
        return X

    def _fit_once(self, feat, y, weights) -> Ridge:
        X = features.linear_design(feat, use_lags=self.use_lags, calendar=self.calendar)
        self._columns = list(X.columns)
        return Ridge(alpha=self.alpha).fit(X, y, sample_weight=weights)

    def fit(self, feat: pd.DataFrame, y: pd.Series, weights: np.ndarray | None) -> None:
        dates = pd.to_datetime(feat["local_date"])
        calib_start = dates.max() - pd.Timedelta(days=self.calib_days - 1)
        fit_part = (dates < calib_start).to_numpy()
        w = None if weights is None else np.asarray(weights)
        self._columns = []
        m = self._fit_once(feat[fit_part], y[fit_part], None if w is None else w[fit_part])
        Xc = features.linear_design(feat[~fit_part], use_lags=self.use_lags,
                                    calendar=self.calendar) \
            .reindex(columns=self._columns, fill_value=0.0)
        resid = y[~fit_part].to_numpy() - m.predict(Xc)
        self._resid_q = tuple(float(np.quantile(resid, q)) for q in self.quantiles)
        self._columns = []
        self._model = self._fit_once(feat, y, w)

    def predict(self, feat: pd.DataFrame) -> pd.DataFrame:
        f = self._model.predict(self._design(feat))
        return pd.DataFrame({"forecast": f, "q10": f + self._resid_q[0],
                             "q90": f + self._resid_q[1]}, index=feat.index)


@dataclass
class GBMQuantileModel:
    """Gradient boosting (scikit-learn HistGradientBoosting) with quantile loss.

    Three models per refit: 10 %, 50 % and 90 % quantiles. The median is the point
    forecast (it minimises absolute error, the business metric). Hyperparameters are
    fixed up front (D18), not tuned on the backtest. Crossing quantiles are sorted
    per row (monotone rearrangement).
    """
    name: str = "gbm_quantile"
    calendar: str = "v1"
    quantiles: tuple[float, float, float] = (0.1, 0.5, 0.9)
    max_iter: int = 400
    learning_rate: float = 0.05
    max_leaf_nodes: int = 31
    min_samples_leaf: int = 40
    l2_regularization: float = 1.0
    seed: int = 20261119
    needs_fit: bool = True
    _models: dict = field(default_factory=dict, repr=False)

    def fit(self, feat: pd.DataFrame, y: pd.Series, weights: np.ndarray | None) -> None:
        from sklearn.ensemble import HistGradientBoostingRegressor

        X, cat = features.tree_design(feat, self.calendar)
        self._models = {}
        for q in self.quantiles:
            m = HistGradientBoostingRegressor(
                loss="quantile", quantile=q, max_iter=self.max_iter,
                learning_rate=self.learning_rate, max_leaf_nodes=self.max_leaf_nodes,
                min_samples_leaf=self.min_samples_leaf,
                l2_regularization=self.l2_regularization, categorical_features=cat,
                early_stopping=False, random_state=self.seed)
            self._models[q] = m.fit(X, y, sample_weight=weights)

    def predict(self, feat: pd.DataFrame) -> pd.DataFrame:
        X, _ = features.tree_design(feat, self.calendar)
        qs = np.column_stack([self._models[q].predict(X) for q in self.quantiles])
        qs.sort(axis=1)
        return pd.DataFrame({"q10": qs[:, 0], "forecast": qs[:, 1], "q90": qs[:, 2]},
                            index=feat.index)
