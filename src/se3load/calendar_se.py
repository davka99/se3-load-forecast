"""Swedish public holidays and de facto holidays, implemented without third-party packages.

Public holidays (allmänna helgdagar) follow Lag (1989:253) om allmänna helgdagar.
De facto holidays (Midsommarafton, Julafton, Nyårsafton) are not legal holidays but
most workplaces are closed, which matters more for electricity use than the law does.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm / Meeus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _saturday_between(year: int, month: int, first_day: int, last_day: int) -> date:
    for day in range(first_day, last_day + 1):
        candidate = date(year, month, day)
        if candidate.weekday() == 5:
            return candidate
    raise ValueError("No Saturday in range")  # pragma: no cover - impossible for 7-day windows


def _all_saints_day(year: int) -> date:
    # Saturday between 31 October and 6 November.
    start = date(year, 10, 31)
    for offset in range(7):
        candidate = start + timedelta(days=offset)
        if candidate.weekday() == 5:
            return candidate
    raise ValueError("No Saturday in range")  # pragma: no cover


def public_holidays(year: int) -> dict[date, str]:
    """Legal Swedish public holidays for a year (Sundays other than holidays excluded)."""
    easter = easter_sunday(year)
    midsummer_day = _saturday_between(year, 6, 20, 26)
    return {
        date(year, 1, 1): "Nyårsdagen",
        date(year, 1, 6): "Trettondedag jul",
        easter - timedelta(days=2): "Långfredagen",
        easter: "Påskdagen",
        easter + timedelta(days=1): "Annandag påsk",
        date(year, 5, 1): "Första maj",
        easter + timedelta(days=39): "Kristi himmelsfärdsdag",
        easter + timedelta(days=49): "Pingstdagen",
        date(year, 6, 6): "Sveriges nationaldag",
        midsummer_day: "Midsommardagen",
        _all_saints_day(year): "Alla helgons dag",
        date(year, 12, 25): "Juldagen",
        date(year, 12, 26): "Annandag jul",
    }


def de_facto_holidays(year: int) -> dict[date, str]:
    """Days that are not legal holidays but where most workplaces are closed."""
    midsummer_eve = _saturday_between(year, 6, 20, 26) - timedelta(days=1)
    return {
        midsummer_eve: "Midsommarafton",
        date(year, 12, 24): "Julafton",
        date(year, 12, 31): "Nyårsafton",
    }


def holiday_frame(start_year: int, end_year: int) -> pd.DataFrame:
    """One row per holiday date with name and kind ('public' or 'de_facto')."""
    rows = []
    for year in range(start_year, end_year + 1):
        rows += [(d, n, "public") for d, n in public_holidays(year).items()]
        rows += [(d, n, "de_facto") for d, n in de_facto_holidays(year).items()]
    out = pd.DataFrame(rows, columns=["date", "name", "kind"])
    out["date"] = pd.to_datetime(out["date"])
    return out.sort_values("date").reset_index(drop=True)


def is_bridge_day(day: date, holidays: set[date]) -> bool:
    """A weekday squeezed between a holiday and a weekend (klämdag)."""
    if day.weekday() >= 5 or day in holidays:
        return False
    prev_day, next_day = day - timedelta(days=1), day + timedelta(days=1)
    prev_off = prev_day.weekday() >= 5 or prev_day in holidays
    next_off = next_day.weekday() >= 5 or next_day in holidays
    return prev_off and next_off
