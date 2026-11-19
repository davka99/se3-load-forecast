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
                 loc="left", color=TEXT_2, fontsize=9)
    for i, v in enumerate(wk.to_numpy()):
        a3.text(i, v + (40 if v >= 0 else -40), f"{v:+.0f}", ha="center",
                va="bottom" if v >= 0 else "top", color=TEXT, fontsize=8)
    for ax in (a1, a2, a3):
        _style(ax)
    fig.suptitle("Daily SE3 load is dominated by the yearly cycle; weekends drop ~1 GW",
                 x=0.125, ha="left", color=TEXT, fontsize=12, fontweight="bold", y=0.98)
    fig.text(0.01, 0.005, SOURCE + " MSTL (statsmodels), periods 7 and 365 days.",
             color=TEXT_2, fontsize=7.5)
    fig.savefig(path, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
