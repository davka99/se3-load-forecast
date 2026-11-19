import numpy as np
import pandas as pd
import pytest

from se3load import explore, metrics, quality


def test_block_bootstrap_detects_clear_difference_and_respects_sign():
    rng = np.random.default_rng(0)
    y = rng.normal(0, 1, 7 * 24 * 20)
    good = y + rng.normal(0, 0.5, len(y))
    bad = y + rng.normal(0, 2.0, len(y))
    blocks = np.repeat(np.arange(20), 7 * 24)
    res = metrics.block_bootstrap_mae_diff(y, good, bad, blocks, n_boot=500, seed=1)
    assert res["diff"] < 0 and res["ci"][1] < 0
    assert res["n_blocks"] == 20
    same = metrics.block_bootstrap_mae_diff(y, good, good, blocks, n_boot=200)
    assert same["diff"] == 0 and same["ci"] == [0.0, 0.0]


def test_daily_load_keeps_only_complete_days():
    ts = pd.date_range("2025-03-29T00:00Z", periods=24 * 3, freq="1h")
    d = quality.add_local_time(pd.DataFrame({"timestamp_utc": ts}))
    d["load_mw"] = 1.0
    daily = explore.daily_load(d)
    # 2025-03-29 local starts at 01:00 (23 h kept); 2025-03-30 has 23 h (DST);
    # 2025-04-01 is a partial day (2 h) -> dropped (NaN or absent).
    assert daily.loc["2025-03-30"] == 1.0
    assert daily.index.freqstr == "D"


def test_mstl_recovers_weekly_pattern():
    pytest.importorskip("statsmodels")
    idx = pd.date_range("2019-01-01", periods=365 * 4, freq="D")
    rng = np.random.default_rng(0)
    # Effect by weekday (Mon=0 .. Sun=6), mapped via dayofweek (2019-01-01 is a Tuesday).
    week = np.array([100, 100, 100, 100, 50, -200, -250])[idx.dayofweek]
    year = 2000 * np.cos(2 * np.pi * idx.dayofyear / 365.25)
    y = pd.Series(9000 + week + year + rng.normal(0, 30, len(idx)), index=idx)
    dec = explore.mstl_daily(y)
    wk = dec["seasonal_7"].groupby(dec.index.dayofweek).mean()
    assert wk.iloc[6] < -150 and wk.iloc[0] > 50
    assert explore.seasonal_strength(dec, "seasonal_365") > 0.9
