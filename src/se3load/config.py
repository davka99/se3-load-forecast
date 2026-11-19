"""Project-wide constants. Every value here is an analysis choice; see docs/decisions.md."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
REPORTS = PROJECT_ROOT / "reports"
FIGURES = REPORTS / "figures"

SEED = 20261119

# Study window (UTC, end exclusive). Training/validation history starts 2019;
# the final 12 months are the held-out backtest period.
START = "2019-01-01"
END = "2026-11-01"
BACKTEST_START = "2025-11-01"

BIDDING_ZONE = "SE3"

# SMHI air temperature stations used for a population-weighted SE3 temperature.
# Weights are approximate county populations (millions, SCB 2024, rounded).
# Only relative size matters; sensitivity to the weights is checked in the
# analysis by comparing against a Stockholm-only temperature.
TEMPERATURE_STATIONS: dict[str, float] = {
    "Stockholm-Observatoriekullen A": 2.45,
    "Göteborg A": 1.75,
    "Linköping-Malmslätt": 0.47,
    "Uppsala Aut": 0.40,
    "Örebro A": 0.31,
    "Västerås": 0.28,
}

# Seasonal period for hourly data (one week) used by the seasonal-naive
# baseline and the MASE scaling.
SEASON_HOURS = 168
