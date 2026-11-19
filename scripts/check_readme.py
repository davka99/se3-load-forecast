"""Check that the key numbers quoted in README.md match the result files.

Usage: python scripts/check_readme.py   (exit code 1 on any mismatch)
Each check formats a value from reports/*.json the way the README writes it and
asserts that exact text occurs in README.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str) -> dict:
    return json.loads((ROOT / "reports" / name).read_text(encoding="utf-8"))


def checks() -> list[tuple[str, str]]:
    bt = load("backtest_results.json")
    reg = load("regression_validation.json")
    m = bt["models"]
    ci = bt["mae_ci95_by_model"]
    prim = bt["primary_average_vs_seasonal_naive"]
    sec = bt["secondary_average_vs_linear"]
    vsg = bt["average_vs_gbm"]
    seg = {(r["segment"], r["model"]): r for r in bt["segments"]}
    sim = bt["weather_noise_simulation"]
    sens = reg["sensitivity_weighted_temp"]
    pre = reg["sensitivity_pre_crisis_2019_2021"]
    slopes = reg["hourly_slopes_weighted_temp"]
    out = []
    for name in ("seasonal_naive_d7", "linear", "gbm_quantile", "average"):
        out.append((f"{name} MAE", f"{m[name]['mae_mw']:.1f} MW ({ci[name][0]:.1f}–{ci[name][1]:.1f})"))
        out.append((f"{name} MAPE", f"{m[name]['mape_pct']:.2f} %"))
    for name in ("linear", "gbm_quantile", "average"):
        out.append((f"{name} MASE", f"{m[name]['mase']:.3f}"))
        out.append((f"{name} coverage", f"{m[name]['intervals']['coverage_80_pct']:.1f} %"))
    out += [
        ("primary pct", f"{-prim['pct_vs_b']:.0f} %"),
        ("primary diff", f"−{-prim['mae_diff_mw']:.1f} MW, 95 % CI −{-prim['ci95_mw'][0]:.1f} to "
                         f"−{-prim['ci95_mw'][1]:.1f}"),
        ("vs linear", f"{-sec['mae_diff_mw']:.1f} MW better\n   than linear (CI "
                      f"{-sec['ci95_mw'][1]:.1f}–{-sec['ci95_mw'][0]:.1f})"),
        ("vs gbm", f"{-vsg['mae_diff_mw']:.1f} MW better than gradient boosting (CI "
                   f"{-vsg['ci95_mw'][1]:.1f}–{-vsg['ci95_mw'][0]:.1f})"),
        ("cold linear bias", f"{-seg[('cold hours (< -5 °C)', 'linear')]['bias_mw']:.0f} MW"),
        ("cold gbm bias", f"{seg[('cold hours (< -5 °C)', 'gbm_quantile')]['bias_mw']:.0f} MW"),
        ("cold avg bias", f"{seg[('cold hours (< -5 °C)', 'average')]['bias_mw']:.0f} MW"),
        ("peak bias", f"{-seg[('daily peak hour', 'average')]['bias_mw']:.0f} MW too low"),
        ("peak coverage", f"{seg[('daily peak hour', 'average')]['coverage_80_pct']:.1f} %"),
        ("sensitivity", f"{sens['mw_per_degc_below_15']:.0f} MW per °C** (95 % CI "
                        f"{sens['ci95'][0]:.0f}–{sens['ci95'][1]:.0f})"),
        ("gwh per day", f"{sens['gwh_per_day_per_degc']:.1f} GWh per day per °C"),
        ("pre-crisis", f"{pre['mw_per_degc_below_15']:.0f} MW/°C, CI {pre['ci95'][0]:.0f}–"
                       f"{pre['ci95'][1]:.0f}"),
        ("slope 17", f"17:00 ({slopes['17']['slope']:.0f} MW/°C)"),
        ("slope 07", f"07:00 ({slopes['7']['slope']:.0f} MW/°C)"),
        ("noise increase", f"raises MAE by {sim['increase_vs_observed_mw']:.0f} MW"),
        ("overall bias", f"overall bias {m['average']['bias_mw']:.0f} MW"),
        ("hero temp", f"{bt['hero_week']['coldest_daily_mean_temp_c']:.1f} °C daily mean"),
    ]
    return out


def main() -> int:
    norm = lambda t: t.replace("\u2212", "-")  # noqa: E731  README uses the minus sign −
    readme = norm((ROOT / "README.md").read_text(encoding="utf-8"))
    failures = [(label, text) for label, text in checks() if norm(text) not in readme]
    for label, text in failures:
        print(f"MISSING {label}: {text!r}")
    print(f"{len(checks()) - len(failures)}/{len(checks())} README numbers match the results")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
