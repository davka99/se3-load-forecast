import numpy as np
import pytest

from se3load import metrics


def test_basic_errors():
    y, f = [100, 200], [110, 180]
    assert metrics.mae(y, f) == pytest.approx(15)
    assert metrics.rmse(y, f) == pytest.approx(np.sqrt((100 + 400) / 2))
    assert metrics.mape(y, f) == pytest.approx(10)


def test_mape_rejects_zero():
    with pytest.raises(ValueError):
        metrics.mape([0, 1], [1, 1])


def test_mase_equals_one_for_seasonal_naive_on_periodic_plus_trend():
    # For a series where seasonal naive error is constant, forecasting with the
    # same seasonal-naive rule out of sample gives MASE = 1.
    season = 4
    y = np.arange(40, dtype=float) + np.tile([0, 5, 0, -5], 10)
    train, test = y[:32], y[32:]
    naive = y[32 - season:40 - season]
    assert metrics.mase(test, naive, train, season) == pytest.approx(1.0)


def test_interval_coverage_and_pinball():
    assert metrics.interval_coverage([1, 2, 3, 4], [0, 0, 0, 5], [2, 2, 2, 6]) == 0.5
    assert metrics.pinball_loss([10], [8], 0.9) == pytest.approx(1.8)
    assert metrics.pinball_loss([10], [12], 0.9) == pytest.approx(0.2)


def test_wilson_interval_known_value():
    lo, hi = metrics.wilson_interval(90, 100)
    assert lo == pytest.approx(0.8256, abs=1e-3)
    assert hi == pytest.approx(0.9448, abs=1e-3)
    with pytest.raises(ValueError):
        metrics.wilson_interval(5, 0)
