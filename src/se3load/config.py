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

# SMHI air temperature stations used for a population-weighted SE3 temperature,
# pinned by station id (names are not unique in SMHI's list, e.g. two "Västerås").
# id -> (expected SMHI name, weight). Weights are approximate county populations
# (millions, SCB 2024, rounded); only relative size matters. Sensitivity to the
# weights is checked against a Stockholm-only temperature. Verified by the probe
# run of 2026-11-19 (all six active and covering 2019 onwards).
TEMPERATURE_STATIONS: dict[int, tuple[str, float]] = {
    98230: ("Stockholm-Observatoriekullen A", 2.45),  # Stockholms län
    71420: ("Göteborg A", 1.75),                      # Västra Götalands län
    85240: ("Linköping-Malmslätt", 0.47),             # Östergötlands län
    97510: ("Uppsala Aut", 0.40),                     # Uppsala län
    95130: ("Örebro Flygplats", 0.31),                # Örebro län ("Örebro A" does not exist)
    96350: ("Västerås", 0.28),                        # Västmanlands län (active id; 28 is a closed namesake)
}


def station_weights() -> dict[str, float]:
    """Station name -> weight, the form used by smhi.weighted_temperature."""
    return {name: w for name, w in TEMPERATURE_STATIONS.values()}


# eSett publishes consumption as negative energy (MWh) per settlement period
# (15 min from at least 2024; checked per year by the fetch manifest).
ESETT_VALUE_KIND = "energy"

# Seasonal period for hourly data (one week) used by the seasonal-naive
# baseline and the MASE scaling.
SEASON_HOURS = 168
