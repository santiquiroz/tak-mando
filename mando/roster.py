from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Player:
    uid: str
    callsign: str
    lat: float
    lon: float
    last_seen: object
    stale: object


class Roster:
    def __init__(self, ignore_uids=(), ignore_prefixes=("overlay-",)):
        self._ignore = set(ignore_uids)
        self._prefixes = tuple(ignore_prefixes)
        self._by_uid = {}

    def update(self, event, now):
        typ = event.get("type") or ""
        if not typ.startswith("a-f-G-U"):
            return None
        callsign = event.get("callsign")
        if not callsign:
            return None
        if isinstance(callsign, str) and callsign.strip() == "":
            return None
        lat = event.get("lat")
        lon = event.get("lon")
        if lat is None or lon is None:
            return None
        if lat == 0 and lon == 0:
            return None
        stale = event.get("stale")
        # On connect the server replays the last position of players who already left; their stale time has passed.
        if stale is not None and stale <= now:
            return None
        uid = event.get("uid")
        if uid in self._ignore:
            return None
        if isinstance(uid, str):
            for prefix in self._prefixes:
                if uid.startswith(prefix):
                    return None
        player = Player(
            uid=uid,
            callsign=callsign,
            lat=lat,
            lon=lon,
            last_seen=now,
            stale=event.get("stale"),
        )
        self._by_uid[uid] = player
        return player

    def contact(self, event, now):
        # A player without a GPS fix reports 0,0: it is not on the map yet, but it is connected and can receive files.
        typ = event.get("type") or ""
        uid = event.get("uid")
        callsign = (event.get("callsign") or "").strip()
        stale = event.get("stale")
        if not typ.startswith("a-f-G-U") or not callsign or not isinstance(uid, str):
            return None
        if stale is not None and stale <= now:
            return None
        if uid in self._ignore or uid.startswith(self._prefixes):
            return None
        return uid, callsign

    def players(self):
        return sorted(self._by_uid.values(), key=lambda p: p.callsign.lower())

    def get(self, uid):
        return self._by_uid.get(uid)

    def find(self, query):
        if not query:
            return None
        q = query.lower()
        for p in self._by_uid.values():
            if p.callsign.lower() == q:
                return p
        matches = [p for p in self._by_uid.values() if p.callsign.lower().startswith(q)]
        if len(matches) == 1:
            return matches[0]
        return None
