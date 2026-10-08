from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

_SYNODIC_MONTH_DAYS = 29.530588853
_NEW_MOON_REF = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)

_MORNING = (
    ("astronomical_dawn", 108.0),
    ("nautical_dawn", 102.0),
    ("civil_dawn", 96.0),
    ("sunrise", 90.833),
)
_EVENING = (
    ("sunset", 90.833),
    ("civil_dusk", 96.0),
    ("nautical_dusk", 102.0),
    ("astronomical_dusk", 108.0),
)


def _mean_longitude(t: float) -> float:
    return 0.9856 * t - 3.289


def _sun_true_longitude(t: float) -> float:
    m = _mean_longitude(t)
    lng = m + 1.916 * math.sin(math.radians(m)) + 0.020 * math.sin(math.radians(2.0 * m)) + 282.634
    return lng % 360.0


def _right_ascension_hours(true_long: float) -> float:
    ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(true_long)))) % 360.0
    # atan loses the quadrant, restore it from the true longitude.
    ra += math.floor(true_long / 90.0) * 90.0 - math.floor(ra / 90.0) * 90.0
    return ra / 15.0


def _hour_angle_hours(lat: float, true_long: float, zenith: float, morning: bool) -> float | None:
    sin_dec = 0.39782 * math.sin(math.radians(true_long))
    cos_dec = math.cos(math.asin(sin_dec))
    denom = cos_dec * math.cos(math.radians(lat))
    if denom == 0.0:
        return None
    cos_h = (math.cos(math.radians(zenith)) - sin_dec * math.sin(math.radians(lat))) / denom
    if cos_h > 1.0 or cos_h < -1.0:
        return None
    angle = math.degrees(math.acos(cos_h))
    if morning:
        angle = 360.0 - angle
    return angle / 15.0


def _ut_hours(day_of_year: int, lon: float, lat: float, zenith: float, morning: bool) -> float | None:
    lng_hour = lon / 15.0
    if morning:
        t = day_of_year + (6.0 - lng_hour) / 24.0
    else:
        t = day_of_year + (18.0 - lng_hour) / 24.0
    true_long = _sun_true_longitude(t)
    h = _hour_angle_hours(lat, true_long, zenith, morning)
    if h is None:
        return None
    ra = _right_ascension_hours(true_long)
    return (h + ra - 0.06571 * t - 6.622 - lng_hour) % 24.0


def _to_local(day: date, ut: float, utc_offset_h: float) -> datetime:
    return datetime(day.year, day.month, day.day) + timedelta(hours=ut + utc_offset_h)


def sun_events(day: date, lat: float, lon: float, utc_offset_h: float) -> dict[str, datetime | None]:
    n = day.timetuple().tm_yday
    events: dict[str, datetime | None] = {}
    for name, zenith in _MORNING:
        ut = _ut_hours(n, lon, lat, zenith, True)
        events[name] = None if ut is None else _to_local(day, ut, utc_offset_h)
    for name, zenith in _EVENING:
        ut = _ut_hours(n, lon, lat, zenith, False)
        events[name] = None if ut is None else _to_local(day, ut, utc_offset_h)
    return events


def moon_illumination(dt_utc: datetime) -> float:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    age = ((dt_utc - _NEW_MOON_REF).total_seconds() / 86400.0) % _SYNODIC_MONTH_DAYS
    return (1.0 - math.cos(2.0 * math.pi * age / _SYNODIC_MONTH_DAYS)) / 2.0
