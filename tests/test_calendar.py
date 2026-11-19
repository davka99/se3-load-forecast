from datetime import date

import pytest

from se3load.calendar_se import (
    de_facto_holidays,
    easter_sunday,
    holiday_frame,
    is_bridge_day,
    public_holidays,
)


@pytest.mark.parametrize(
    "year, expected",
    [(2019, date(2019, 4, 21)), (2020, date(2020, 4, 12)), (2024, date(2024, 3, 31)),
     (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5))],
)
def test_easter_known_dates(year, expected):
    assert easter_sunday(year) == expected


def test_public_holidays_2025_match_official_calendar():
    h = public_holidays(2025)
    assert len(h) == 13
    assert h[date(2025, 4, 18)] == "Långfredagen"
    assert h[date(2025, 5, 29)] == "Kristi himmelsfärdsdag"
    assert h[date(2025, 6, 21)] == "Midsommardagen"
    assert h[date(2025, 11, 1)] == "Alla helgons dag"


def test_de_facto_midsummer_eve_is_friday():
    for year in range(2019, 2027):
        eve = [d for d, n in de_facto_holidays(year).items() if n == "Midsommarafton"][0]
        assert eve.weekday() == 4
        assert 19 <= eve.day <= 25


def test_holiday_frame_has_no_duplicate_dates_within_kind():
    df = holiday_frame(2019, 2026)
    assert not df.duplicated(subset=["date", "kind"]).any()
    assert set(df["kind"]) == {"public", "de_facto"}


def test_bridge_day_after_ascension():
    hol = set(public_holidays(2025))
    # Ascension Thursday 29 May 2025 -> Friday 30 May is a klämdag.
    assert is_bridge_day(date(2025, 5, 30), hol)
    assert not is_bridge_day(date(2025, 5, 28), hol)
    assert not is_bridge_day(date(2025, 5, 31), hol)  # Saturday
