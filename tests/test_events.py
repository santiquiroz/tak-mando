from datetime import datetime, timedelta, timezone

from mando.events import EventLog

NOW = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)


def test_since_filters_by_minutes_and_keeps_order():
    log = EventLog()
    log.add("conexion", "A entró", NOW - timedelta(minutes=20))
    log.add("mapa", "B marcó", NOW - timedelta(minutes=5))
    log.add("peligro", "C en tanque", NOW)
    assert [e.text for e in log.since(NOW, 10)] == ["B marcó", "C en tanque"]


def test_maxlen_drops_oldest():
    log = EventLog(maxlen=3)
    for i in range(5):
        log.add("mapa", str(i), NOW)
    assert [e.text for e in log.since(NOW, 60)] == ["2", "3", "4"]


def test_roundtrip_list():
    log = EventLog()
    log.add("mapa", "x", NOW)
    again = EventLog.from_list(log.to_list())
    assert [(e.kind, e.text, e.at) for e in again.since(NOW, 1)] == [("mapa", "x", NOW)]
