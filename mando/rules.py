from __future__ import annotations

from datetime import timedelta

from mando.geo import point_in_ring
from mando.weather import next_hours


class GeofenceTracker:
    def __init__(self, zones, cooldown_s=180):
        self._zones = list(zones)
        self._cooldown = cooldown_s
        self._prev = {}
        self._last = {}

    def check(self, player, now):
        inside = set()
        for idx, zone in enumerate(self._zones):
            try:
                hit = point_in_ring(player.lat, player.lon, zone.ring)
            except (TypeError, IndexError):
                hit = False
            if hit:
                inside.add(idx)
        prev = self._prev.get(player.uid, set())
        out = []
        for idx in sorted(inside - prev):
            last = self._last.get((player.uid, idx))
            if last is not None and (now - last).total_seconds() < self._cooldown:
                continue
            zone = self._zones[idx]
            if zone.message:
                out.append(f"\u26a0 PELIGRO: {zone.name}. {zone.message}")
            else:
                out.append(f"\u26a0 PELIGRO: {zone.name}.")
            self._last[(player.uid, idx)] = now
        self._prev[player.uid] = inside
        return out


class LostContactTracker:
    def __init__(self, after_s=300, recent_s=3600):
        self._after = after_s
        self._recent = recent_s
        self._flagged = {}

    def check(self, players, now):
        if self._after == 0:
            return []
        out = []
        for p in players:
            flagged_at = self._flagged.get(p.uid)
            if flagged_at is not None:
                if p.last_seen > flagged_at:
                    del self._flagged[p.uid]
                else:
                    continue
            age = (now - p.last_seen).total_seconds()
            if age > self._after and age <= self._recent:
                out.append(p)
                self._flagged[p.uid] = now
        return out


def light_schedule(events, moon):
    out = []
    sunset = events.get("sunset")
    nautical = events.get("nautical_dusk")
    sunrise = events.get("sunrise")
    if sunset is not None:
        out.append((
            sunset - timedelta(minutes=15),
            f"Puesta del sol en 15 min ({sunset:%H:%M}). "
            "Ocupen posiciones nocturnas con la \u00faltima luz.",
        ))
        if nautical is not None:
            out.append((
                sunset,
                f"Se puso el sol. Oscuridad total hacia las {nautical:%H:%M}.",
            ))
    if nautical is not None:
        out.append((
            nautical,
            f"Oscuridad total. Luna al {moon:.0%}. "
            "Disciplina de luz: solo luz roja, pantallas al m\u00ednimo.",
        ))
    if sunrise is not None:
        out.append((
            sunrise - timedelta(minutes=15),
            f"Amanece a las {sunrise:%H:%M}.",
        ))
    out.sort(key=lambda item: item[0])
    return out


class Announcer:
    def __init__(self, grace_s=600):
        self._grace = grace_s
        self._seen = set()

    def due(self, schedule, now_local):
        out = []
        for when, text in schedule:
            if when > now_local:
                continue
            if (now_local - when).total_seconds() > self._grace:
                continue
            key = (when, text)
            if key in self._seen:
                continue
            self._seen.add(key)
            out.append(text)
        return out


class RainWatch:
    def __init__(self, threshold_mm=2.0, min_interval_s=7200):
        self._threshold = threshold_mm
        self._interval = min_interval_s
        self._last = None

    def check(self, hours, now_utc, utc_offset_h):
        target = None
        for h in next_hours(hours, now_utc, 2):
            if h.rain_mm is not None and h.rain_mm >= self._threshold:
                target = h
                break
        if target is None:
            return None
        if self._last is not None and (now_utc - self._last).total_seconds() < self._interval:
            return None
        self._last = now_utc
        local = target.time + timedelta(hours=utc_offset_h)
        return (
            f"Lluvia fuerte prevista hacia las {local:%H:%M} "
            f"({target.rain_mm:.1f} mm/h). Protejan equipos y radares."
        )
