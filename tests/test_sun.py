from datetime import date, datetime, timezone

from mando.sun import moon_illumination, sun_events

LAT = 5.1606
LON = -75.4918
OFFSET = -5
DAY = date(2026, 10, 10)


def _close(dt, hour, minute, tol_min):
    assert dt is not None
    got = dt.hour * 60 + dt.minute + dt.second / 60.0
    assert abs(got - (hour * 60 + minute)) <= tol_min, dt


def test_neira_sunrise_sunset():
    ev = sun_events(DAY, LAT, LON, OFFSET)
    _close(ev["sunrise"], 5, 48, 2)
    _close(ev["sunset"], 17, 49, 2)


def test_neira_dusk_values():
    ev = sun_events(DAY, LAT, LON, OFFSET)
    _close(ev["civil_dusk"], 18, 12, 3)
    _close(ev["nautical_dusk"], 18, 36, 3)


def test_dawn_before_sunrise_dusk_after_sunset():
    ev = sun_events(DAY, LAT, LON, OFFSET)
    assert ev["astronomical_dawn"] < ev["nautical_dawn"] < ev["civil_dawn"] < ev["sunrise"]
    assert ev["sunset"] < ev["civil_dusk"] < ev["nautical_dusk"] < ev["astronomical_dusk"]


def test_polar_day_has_no_sunset():
    ev = sun_events(date(2026, 6, 21), 78.0, 0.0, 0)
    assert ev["sunset"] is None


def test_moon_new_and_full():
    new = moon_illumination(datetime(2026, 10, 10, 23, 0, tzinfo=timezone.utc))
    assert new < 0.03
    full = moon_illumination(datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc))
    assert full > 0.9
