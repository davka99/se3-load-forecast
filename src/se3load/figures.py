"""Static figures for README and reports (matplotlib, PNG).

Palette: first slots of the validated categorical palette from the dataviz skill
(blue #2a78d6, orange #eb6834) on a near-white surface, muted grid, no dual axes.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
MUTED_BAR = "#b9c6d6"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
SOURCE = "Source: eSett Open Data (SE3 settled consumption), SMHI (air temperature). Own calculations."


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_2, labelsize=9)


def overview(data: pd.DataFrame, path: Path) -> None:
    """Two stacked panels on a shared time axis: weekly mean load and temperature."""
    d = data.set_index("timestamp_utc")[["load_mw", "temperature_c"]]
    weekly = d.resample("W").mean().iloc[1:-1]  # drop partial first/last weeks
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 5.6), sharex=True, dpi=150,
                                 gridspec_kw={"height_ratios": [3, 2], "hspace": 0.12})
    fig.patch.set_facecolor(SURFACE)
    a1.plot(weekly.index, weekly["load_mw"] / 1000, color=BLUE, linewidth=2)
    a1.set_ylabel("GW (weekly mean)", color=TEXT_2, fontsize=9)
    a1.set_ylim(0, None)
    a2.plot(weekly.index, weekly["temperature_c"], color=ORANGE, linewidth=2)
    a2.axhline(0, color=TEXT_2, linewidth=0.8)
    a2.set_ylabel("°C (weekly mean)", color=TEXT_2, fontsize=9)
    for ax in (a1, a2):
        _style(ax)
    a1.set_title("SE3 consumption peaks in the coldest weeks of each winter",
                 loc="left", color=TEXT, fontsize=12, fontweight="bold", pad=18)
    a1.text(0, 1.01, "Electricity consumption, bidding zone SE3", transform=a1.transAxes,
            color=TEXT_2, fontsize=9, va="bottom")
    a2.text(0, 1.01, "Population-weighted air temperature, 5 SMHI stations",
            transform=a2.transAxes, color=TEXT_2, fontsize=9, va="bottom")
    fig.text(0.01, 0.005, SOURCE, color=TEXT_2, fontsize=7.5)
    fig.savefig(path, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def load_vs_temperature(data: pd.DataFrame, path: Path,
                        before: tuple[str, str] = ("2019-01-01", "2021-12-31"),
                        after: tuple[str, str] = ("2023-01-01", "2026-10-31")) -> None:
    """Daily mean load vs daily mean temperature, workdays only, two periods."""
    d = data.copy()
    d["date"] = pd.to_datetime(d["local_date"])
    daily = d.groupby("date").agg(load=("load_mw", "mean"), temp=("temperature_c", "mean"),
                                  hours=("load_mw", "size"),
                                  holiday=("is_holiday_or_eve", "max"))
    daily = daily[(daily["hours"] >= 23) & (~daily["holiday"]) & (daily.index.weekday < 5)]
    fig, ax = plt.subplots(figsize=(8, 5.4), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    for (lo, hi), color, label in [(before, BLUE, f"{before[0][:4]}–{before[1][:4]}"),
                                   (after, ORANGE, f"{after[0][:4]}–{after[1][:4]}")]:
        p = daily.loc[lo:hi]
        ax.scatter(p["temp"], p["load"] / 1000, s=10, color=color, alpha=0.55,
                   edgecolors=SURFACE, linewidths=0.4, label=label)
    _style(ax)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_xlabel("Daily mean temperature (°C)", color=TEXT_2, fontsize=9)
    ax.set_ylabel("Daily mean consumption (GW)", color=TEXT_2, fontsize=9)
    ax.set_title("Load rises as it gets colder, and cold days use less power after 2022",
                 loc="left", color=TEXT, fontsize=11, fontweight="bold", pad=18)
    ax.text(0, 1.01, "SE3, working days excluding holidays; 2022 omitted (energy-crisis year)",
            transform=ax.transAxes, color=TEXT_2, fontsize=8.5, va="bottom")
    leg = ax.legend(frameon=False, fontsize=9, loc="upper right", markerscale=2)
    for t in leg.get_texts():
        t.set_color(TEXT)
    fig.text(0.01, -0.02, SOURCE, color=TEXT_2, fontsize=7.5)
    fig.savefig(path, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def decomposition(dec: pd.DataFrame, strengths: dict, path: Path) -> None:
    """MSTL of daily mean load: trend, yearly component, weekly profile."""
    fig = plt.figure(figsize=(10, 7.2), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    gs = fig.add_gridspec(3, 1, height_ratios=[1, 1, 1.1], hspace=0.55)
    a1, a2, a3 = (fig.add_subplot(gs[i]) for i in range(3))
    a1.plot(dec.index, dec["trend"] / 1000, color=BLUE, linewidth=2)
    a1.set_ylabel("GW", color=TEXT_2, fontsize=9)
    a1.set_title(f"Trend (strength {strengths['trend']:.2f}) – includes multi-year weather "
                 "swings, not only structural change", loc="left", color=TEXT_2, fontsize=9)
    a2.plot(dec.index, dec["seasonal_365"] / 1000, color=BLUE, linewidth=1.5)
    a2.axhline(0, color=TEXT_2, linewidth=0.8)
    a2.set_ylabel("GW", color=TEXT_2, fontsize=9)
    a2.set_title(f"Yearly component (strength {strengths['seasonal_365']:.2f})",
                 loc="left", color=TEXT_2, fontsize=9)
    wk = dec["seasonal_7"].groupby(dec.index.dayofweek).mean()
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    a3.bar(labels, wk.to_numpy(), color=BLUE, width=0.6)
    a3.axhline(0, color=TEXT_2, linewidth=0.8)
    a3.set_ylabel("MW vs average day", color=TEXT_2, fontsize=9)
    a3.set_title(f"Weekly component, mean by weekday (strength {strengths['seasonal_7']:.2f})",
                 loc="left", color=TEXT_2, fontsize=9, pad=10)
    span = wk.max() - wk.min()
    a3.set_ylim(wk.min() - 0.25 * span, wk.max() + 0.25 * span)
    for i, v in enumerate(wk.to_numpy()):
        a3.text(i, v + (0.04 * span if v >= 0 else -0.04 * span), f"{v:+.0f}", ha="center",
                va="bottom" if v >= 0 else "top", color=TEXT, fontsize=8)
    for ax in (a1, a2, a3):
        _style(ax)
    fig.suptitle("Daily SE3 load: a strong yearly cycle, weekends ~0.9 GW below weekdays",
                 x=0.125, ha="left", color=TEXT, fontsize=12, fontweight="bold", y=0.98)
    fig.text(0.01, 0.005, SOURCE + " MSTL (statsmodels), periods 7 and 365 days.",
             color=TEXT_2, fontsize=7.5)
    fig.savefig(path, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def hero_week(pred: pd.DataFrame, path: Path, title: str, subtitle: str) -> None:
    """Forecast vs actual with the 80 % band for one week (one model)."""
    p = pred.sort_values("timestamp_utc")
    x = pd.to_datetime(p["timestamp_utc"]).dt.tz_convert("Europe/Stockholm")
    fig, ax = plt.subplots(figsize=(10, 4.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.fill_between(x, p["q10"] / 1000, p["q90"] / 1000, color=BLUE, alpha=0.18,
                    linewidth=0, label="80 % forecast interval")
    ax.plot(x, p["forecast"] / 1000, color=BLUE, linewidth=2, label="Forecast (issued 12:00 day before)")
    ax.plot(x, p["actual"] / 1000, color=TEXT, linewidth=1.4, linestyle=(0, (3, 2)),
            label="Actual")
    _style(ax)
    lo = min(p["q10"].min(), p["actual"].min()) / 1000
    hi = max(p["q90"].max(), p["actual"].max()) / 1000
    ax.set_ylim(lo - 0.05 * (hi - lo), hi + 0.22 * (hi - lo))  # headroom for the legend
    ax.set_ylabel("GW", color=TEXT_2, fontsize=9)
    ax.set_title(title, loc="left", color=TEXT, fontsize=12, fontweight="bold", pad=18)
    ax.text(0, 1.01, subtitle, transform=ax.transAxes, color=TEXT_2, fontsize=9, va="bottom")
    leg = ax.legend(frameon=False, fontsize=8.5, loc="upper left", ncol=3)
    for t in leg.get_texts():
        t.set_color(TEXT)
    import matplotlib.dates as mdates
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b", tz=x.dt.tz))
    fig.text(0.01, -0.02, SOURCE, color=TEXT_2, fontsize=7.5)
    fig.savefig(path, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def mae_by_model(rows: list[dict], path: Path, title: str, subtitle: str = "",
                 source: str = SOURCE, highlight: str | None = None) -> None:
    """Horizontal bars: backtest MAE per model with 95 % block-bootstrap CI.

    ``highlight`` is the label of the bar to emphasise (the others are drawn muted);
    with None every bar is drawn in the same colour.
    """
    rows = sorted(rows, key=lambda r: r["mae"], reverse=True)
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    y = range(len(rows))
    colors = [BLUE if highlight is None or r["label"] == highlight else MUTED_BAR for r in rows]
    ax.barh(list(y), [r["mae"] for r in rows], color=colors, height=0.55)
    for i, r in enumerate(rows):
        ax.plot(r["ci"], [i, i], color=TEXT, linewidth=1.2)
        ax.text(r["ci"][1] + 12, i, f"{r['mae']:.0f} MW", va="center", color=TEXT, fontsize=9)
    ax.set_yticks(list(y), [r["label"] for r in rows])
    ax.set_xlim(0, max(r["ci"][1] for r in rows) * 1.18)
    _style(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_xlabel("Mean absolute error per hour (MW); line = 95 % weekly-block bootstrap CI",
                  color=TEXT_2, fontsize=8.5)
    ax.set_title(title, loc="left", color=TEXT, fontsize=11, fontweight="bold", pad=18)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, color=TEXT_2, fontsize=9, va="bottom")
    fig.text(0.01, -0.09, source, color=TEXT_2, fontsize=7.5)
    fig.savefig(path, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


SOURCE_FORECAST = (SOURCE.removesuffix(". Own calculations.")
                   + ", Open-Meteo.com (archived temperature forecasts, CC BY 4.0). Own calculations.")


def coverage_before_after(rows: list[dict], path: Path, title: str, subtitle: str,
                          source: str = SOURCE_FORECAST) -> None:
    """Grouped horizontal bars: 80 % interval coverage before and after recalibration,
    with Wilson 95 % CIs on days and a reference line at the nominal 80 %."""
    fig, ax = plt.subplots(figsize=(8, 3.2), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    h = 0.36
    for i, r in enumerate(rows):
        for j, (key, color, label) in enumerate((
                ("before", BLUE, "The model's own intervals"),
                ("after", ORANGE, "Recalibrated on the last 90 days"))):
            y = i + (h / 2 if j == 0 else -h / 2)
            v = r[key]["coverage_80_pct"]
            ax.barh(y, v, height=h * 0.92, color=color, label=label if i == 0 else None)
            lo, hi = r[key]["wilson95_on_days_pct"]
            ax.plot([lo, hi], [y, y], color=TEXT, linewidth=1.0)
            # Value inside the bar end, so it never collides with the whisker or the 80 % line.
            ax.text(min(v, lo) - 1.5, y, f"{v:.0f} %", va="center", ha="right",
                    color="white", fontsize=8.5, fontweight="bold")
    ax.axvline(80, color=TEXT_2, linewidth=1, linestyle=(0, (4, 3)))
    ax.text(80.8, -0.62, "nominal 80 %", color=TEXT_2, fontsize=8, ha="left", va="center")
    ax.set_yticks(range(len(rows)), [r["label"] for r in rows])
    ax.set_xlim(0, 105)
    _style(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_xlabel("Share of hours inside the 80 % interval (%); line = Wilson 95 % CI on days",
                  color=TEXT_2, fontsize=8.5)
    ax.set_ylim(-0.8, len(rows) - 0.4)
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.2), ncol=2, frameon=False, fontsize=8.5)
    ax.set_title(title, loc="left", color=TEXT, fontsize=11, fontweight="bold", pad=18)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, color=TEXT_2, fontsize=9, va="bottom")
    fig.text(0.01, -0.2, source, color=TEXT_2, fontsize=7.5)
    fig.savefig(path, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
