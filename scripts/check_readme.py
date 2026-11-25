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
    return out + extension_checks()


def extension_checks() -> list[tuple[str, str]]:
    """Numbers from the post-backtest extensions (D20, D21)."""
    if not (ROOT / "reports" / "extensions.json").exists():
        return []
    ex = load("extensions.json")
    am = ex["d20"]["average_model"]
    inc = ex["d20"]["increase_vs_observed_mw"]
    out = []
    for v, label in (("forecast_day1_corrected", "~24 h runs, bias-corrected"),
                     ("forecast_day2_corrected", "~48 h runs, bias-corrected"),
                     ("forecast_day2_raw", "~48 h runs, raw")):
        r = am[v]
        out.append((f"D20 {v}", f"| {label} | {r['mae_mw']:.1f} MW ({r['mae_ci95_mw'][0]:.1f}–"
                                f"{r['mae_ci95_mw'][1]:.1f}) | +{inc[v]:.1f} MW | "
                                f"−{-r['pct_vs_seasonal_naive']:.1f} % |"))
    lo, hi = am["forecast_day1_corrected"], am["forecast_day2_corrected"]
    out.append(("D20 range", f"{lo['mae_mw']:.0f}–{hi['mae_mw']:.0f} MW per hour"))
    out.append(("D20 pct range", f"{-hi['pct_vs_seasonal_naive']:.0f}–"
                                 f"{-lo['pct_vs_seasonal_naive']:.0f} % below the naive rule"))
    out.append(("D20 increase", f"raises MAE by {inc['forecast_day1_corrected']:.0f}–"
                                f"{inc['forecast_day2_corrected']:.0f} MW"))
    te = ex["d20"]["temperature_forecast_error_backtest_year"]
    out.append(("D20 temp error", f"{te['day1_corrected']['mae_c']:.1f}–"
                                  f"{te['day2_corrected']['mae_c']:.1f} °C"))
    ob = ex["d21"]["observed_temperature"]
    out.append(("D21 pred peak bias", f"the bias is −{-ob['predicted daily peak hour']['point']['before']['bias_mw']:.0f} MW"))
    out.append(("D21 daily max obs", f"too low by {-ob['daily_maximum']['before']['bias_mw']:.0f} MW"))
    fc = ex["d21"]["forecast_day2_corrected"]
    rows = (("all hours", "80 % coverage, all hours"),
            ("predicted daily peak hour", "80 % coverage, forecast peak hour"),
            ("actual daily peak hour", "80 % coverage, actual peak hour"))
    for key, label in rows:
        b, a = fc[key]["before"]["coverage_80_pct"], fc[key]["after"]["coverage_80_pct"]
        out.append((f"D21 {key}", f"| {label} | {b:.1f} % | {a:.1f} % |"))
    dm = fc["daily_maximum"]
    out.append(("D21 dm bias", f"| Bias of the forecast daily maximum | −{-dm['before']['bias_mw']:.1f} MW | "
                               f"−{-dm['after']['bias_mw']:.1f} MW |"))
    out.append(("D21 dm mae", f"| MAE of the forecast daily maximum | {dm['before']['mae_mw']:.1f} MW | "
                              f"{dm['after']['mae_mw']:.1f} MW |"))
    out.append(("D21 rec bias", f"from −{-dm['before']['bias_mw']:.0f} to\n  −{-dm['after']['bias_mw']:.0f} MW"))
    out.append(("D21 rec cov", f"from {fc['all hours']['before']['coverage_80_pct']:.0f} % to "
                               f"{fc['all hours']['after']['coverage_80_pct']:.0f} % coverage"))
    out.append(("D21 margin", f"(about {dm['median_level_margin_mw']:.0f} MW)"))
    out.append(("D21 actual peak after", f"actual-peak coverage ({fc['actual daily peak hour']['after']['coverage_80_pct']:.0f} %)"))
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
