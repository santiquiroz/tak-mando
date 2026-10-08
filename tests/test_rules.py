from datetime import date, datetime, timedelta, timezone

from mando.roster import Player
from mando.rules import (
    Announcer,
    GeofenceTracker,
    LostContactTracker,
    RainWatch,
    light_schedule,
)
from mando.sun import sun_events
from mando.weather import Hour
from mando.zones import Zone

NOW = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)
RING = [[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01]]


def _player(uid="p1", lat=0.005, lon=0.005, seen=None):
    return Player(
        uid=uid,
        callsign=uid,
        lat=lat,
        lon=lon,
        last_seen=seen or NOW,
        stale=None,
    )


def test_geofence_entry_only_and_cooldown():
    zone = Zone(name="Tanque", ring=RING, message="No entrar")
    tracker = GeofenceTracker([zone], cooldown_s=100)
    inside = _player()
    outside = _player(lat=0.05, lon=0.05)
    assert tracker.check(inside, NOW) == ["\u26a0 PELIGRO: Tanque. No entrar"]
    assert tracker.check(inside, NOW + timedelta(seconds=10)) == []
    assert tracker.check(outside, NOW + timedelta(seconds=20)) == []
    assert tracker.check(inside, NOW + timedelta(seconds=30)) == []
    assert tracker.check(outside, NOW + timedelta(seconds=40)) == []
    again = tracker.check(inside, NOW + timedelta(seconds=500))
    assert again == ["\u26a0 PELIGRO: Tanque. No entrar"]


def test_geofence_message_without_zone_message():
    zone = Zone(name="Borde", ring=RING, message="")
    tracker = GeofenceTracker([zone])
    assert tracker.check(_player(), NOW) == ["\u26a0 PELIGRO: Borde."]


def test_lost_contact_once_and_rearm():
    tracker = LostContactTracker(after_s=300, recent_s=3600)
    old = _player(seen=NOW - timedelta(seconds=600))
    assert tracker.check([old], NOW) == [old]
    assert tracker.check([old], NOW + timedelta(seconds=60)) == []
    fresh = _player(seen=NOW + timedelta(seconds=61))
    assert tracker.check([fresh], NOW + timedelta(seconds=61)) == []
    stale_again = _player(seen=NOW + timedelta(seconds=61))
    later = NOW + timedelta(seconds=61 + 600)
    assert tracker.check([stale_again], later) == [stale_again]


def test_lost_contact_ignores_old_and_disabled():
    tracker = LostContactTracker(after_s=300, recent_s=3600)
    ancient = _player(seen=NOW - timedelta(seconds=7200))
    assert tracker.check([ancient], NOW) == []
    disabled = LostContactTracker(after_s=0)
    old = _player(seen=NOW - timedelta(seconds=600))
    assert disabled.check([old], NOW) == []


def test_light_schedule_neira():
    events = sun_events(date(2026, 10, 10), 5.1606, -75.4918, -5)
    moon = 0.02
    sched = light_schedule(events, moon)
    assert len(sched) == 4
    times = [t for t, _ in sched]
    assert times == sorted(times)
    by_text = {text: when for when, text in sched}
    sunset = events["sunset"]
    nautical = events["nautical_dusk"]
    sunrise = events["sunrise"]
    first = f"Puesta del sol en 15 min ({sunset:%H:%M}). Ocupen posiciones nocturnas con la \u00faltima luz."
    second = f"Se puso el sol. Oscuridad total hacia las {nautical:%H:%M}."
    third = f"Oscuridad total. Luna al {moon:.0%}. Disciplina de luz: solo luz roja, pantallas al m\u00ednimo."
    fourth = f"Amanece a las {sunrise:%H:%M}."
    assert by_text[first] == sunset - timedelta(minutes=15)
    assert by_text[second] == sunset
    assert by_text[third] == nautical
    assert by_text[fourth] == sunrise - timedelta(minutes=15)


def test_light_schedule_skips_missing():
    assert light_schedule({}, 0.5) == []
    sunset = datetime(2026, 10, 10, 17, 49)
    partial = light_schedule({"sunset": sunset}, 0.1)
    assert len(partial) == 1
    assert partial[0][0] == sunset - timedelta(minutes=15)


def test_announcer_once_and_grace():
    ann = Announcer(grace_s=600)
    base = datetime(2026, 10, 10, 18, 0)
    sched = [(base, "a"), (base + timedelta(minutes=5), "b")]
    now = base + timedelta(minutes=5)
    assert ann.due(sched, now) == ["a", "b"]
    assert ann.due(sched, now) == []
    ann2 = Announcer(grace_s=600)
    old = [(base, "old")]
    assert ann2.due(old, base + timedelta(seconds=601)) == []
    assert ann2.due(old, base + timedelta(seconds=600)) == ["old"]


def _hour(when, rain):
    return Hour(
        time=when, temp_c=20.0, rain_prob=10, rain_mm=rain,
        cloud=10, visibility_m=10000.0, gust_kmh=5.0,
    )


def test_rainwatch_fires_and_cooldown():
    watch = RainWatch(threshold_mm=2.0, min_interval_s=7200)
    now = datetime(2026, 10, 10, 22, 15, tzinfo=timezone.utc)
    hours = [
        _hour(datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc), 0.0),
        _hour(datetime(2026, 10, 10, 23, 0, tzinfo=timezone.utc), 3.2),
    ]
    msg = watch.check(hours, now, -5)
    assert msg == "Lluvia fuerte prevista hacia las 18:00 (3.2 mm/h). Protejan equipos y radares."
    assert watch.check(hours, now + timedelta(minutes=10), -5) is None


def test_rainwatch_silent_below_threshold():
    watch = RainWatch(threshold_mm=2.0)
    now = datetime(2026, 10, 10, 22, 15, tzinfo=timezone.utc)
    hours = [
        _hour(datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc), 1.9),
        _hour(datetime(2026, 10, 10, 23, 0, tzinfo=timezone.utc), 0.5),
    ]
    assert watch.check(hours, now, -5) is None
    exact = [
        _hour(datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc), 2.0),
    ]
    assert watch.check(exact, now, -5) is not None


def test_geofence_set_zones_keeps_state_by_name():
    from mando.rules import GeofenceTracker
    from mando.zones import Zone
    from mando.roster import Player
    from datetime import datetime, timezone
    now = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)
    ring = [[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]]
    other = [[1.0, 1.0], [1.01, 1.0], [1.01, 1.01], [1.0, 1.01], [1.0, 1.0]]
    tracker = GeofenceTracker([Zone("A", ring, "")])
    inside = Player("u1", "x", 0.005, 0.005, now, None)
    assert tracker.check(inside, now) == ["⚠ PELIGRO: A."]
    tracker.set_zones([Zone("B", other, ""), Zone("A", ring, "")])
    assert tracker.check(inside, now) == []
