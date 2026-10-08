"""Thread-safe in-memory event log for SITREP."""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock


@dataclass
class Event:
    at: datetime
    kind: str
    text: str


class EventLog:
    def __init__(self, maxlen=300):
        self._items = deque(maxlen=maxlen)
        self._lock = Lock()

    def add(self, kind, text, now):
        with self._lock:
            self._items.append(Event(at=now, kind=kind, text=text))

    def since(self, now, minutes):
        cutoff = now - timedelta(minutes=minutes)
        with self._lock:
            return [e for e in self._items if e.at >= cutoff]

    def to_list(self):
        with self._lock:
            return [{"at": _iso(e.at), "kind": e.kind, "text": e.text} for e in self._items]

    @staticmethod
    def from_list(items):
        log = EventLog()
        for item in items or []:
            log.add(item.get("kind", ""), item.get("text", ""), _parse(item.get("at", "")))
        return log


def _iso(at):
    if at.tzinfo is None:
        return at.replace(tzinfo=timezone.utc).isoformat()
    return at.astimezone(timezone.utc).isoformat()


def _parse(raw):
    text = str(raw or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if at.tzinfo is None:
        return at.replace(tzinfo=timezone.utc)
    return at
