import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from mando import cot
from mando.bot import Bot, read_package
from mando.grid import Grid
from mando.weather import ForecastCache
from mando.zones import Place, Zone

NOW = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)
RING = [[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]]
PREF = "connectString0=tak.example.com:8089:ssl\nclientPassword=secret123\n"


def _package(path, nested=False, pref=PREF, with_pref=True):
    if nested:
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z:
            if with_pref:
                z.writestr("inner/mando.pref", pref)
            z.writestr("inner/mando-client.p12", b"fake-client")
            z.writestr("inner/truststore-mando.p12", b"fake-trust")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("outer/bundle.zip", inner.getvalue())
    else:
        with zipfile.ZipFile(path, "w") as z:
            if with_pref:
                z.writestr("mando.pref", pref)
            z.writestr("mando-client.p12", b"fake-client")
            z.writestr("truststore-mando.p12", b"fake-trust")
    return str(path)


def test_read_package(tmp_path):
    info = read_package(_package(tmp_path / "pkg.zip"))
    assert info.host == "tak.example.com"
    assert info.port == 8089
    assert info.password == "secret123"
    assert info.client_p12 == b"fake-client"
    assert info.trust_p12 == b"fake-trust"


def test_read_package_nested_zip(tmp_path):
    info = read_package(_package(tmp_path / "pkg.zip", nested=True))
    assert (info.host, info.port, info.password) == (
        "tak.example.com", 8089, "secret123")
    assert info.client_p12 == b"fake-client"
    assert info.trust_p12 == b"fake-trust"


def test_read_package_default_password(tmp_path):
    pref = "connectString0=10.0.0.9:8089:ssl\n"
    info = read_package(_package(tmp_path / "pkg.zip", pref=pref))
    assert info.password == "atakatak"
    assert (info.host, info.port) == ("10.0.0.9", 8089)


def test_read_package_missing_pref_raises(tmp_path):
    with pytest.raises(ValueError):
        read_package(_package(tmp_path / "pkg.zip", with_pref=False))


def _bot(**kw):
    params = dict(
        uid="mando-bot", callsign="Mando", lat=0.005, lon=0.005,
        utc_offset_h=-5,
        zones=[Zone(name="Tanque", ring=RING, message="No entrar")],
        places=[], forecast_cache=ForecastCache(lambda: []),
        announce=False,
    )
    params.update(kw)
    return Bot(**params)


def _pos(uid="p1", callsign="Recon", lat=0.005, lon=0.005, when=NOW):
    return {
        "uid": uid, "type": "a-f-G-U-C-I", "how": "h-g-i-g-o",
        "time": when, "stale": when + timedelta(hours=1),
        "lat": lat, "lon": lon, "callsign": callsign,
        "chat": None, "dest_uids": [],
    }


def _chat(text, sender_uid="p1", sender_callsign="Recon",
          room_name="All Chat Rooms", room_id="All Chat Rooms", when=NOW):
    return cot.parse_event(cot.chat_event(
        sender_uid, sender_callsign, room_name, room_id, text, when))


def test_geofence_entry_sends_dm():
    bot = _bot()
    assert bot.handle_event(
        _pos(lat=0.05, lon=0.05), NOW - timedelta(minutes=1)) == []
    outs = bot.handle_event(_pos(), NOW)
    assert len(outs) == 1
    parsed = cot.parse_event(outs[0])
    assert parsed is not None
    assert parsed["dest_uids"] == ["p1"]
    assert parsed["chat"]["text"].startswith("⚠")


def test_luz_to_all_chat_gets_all_chat_reply():
    bot = _bot()
    bot.handle_event(_pos(), NOW)
    outs = bot.handle_event(_chat("!luz"), NOW)
    assert len(outs) == 1
    parsed = cot.parse_event(outs[0])
    assert parsed["chat"]["room_id"] == "All Chat Rooms"
    assert parsed["dest_uids"] == []


def test_luz_by_dm_gets_dm_reply():
    bot = _bot()
    bot.handle_event(_pos(), NOW)
    incoming = cot.parse_event(cot.dm_event(
        "p1", "Recon", "mando-bot", "Mando", "!luz", NOW))
    outs = bot.handle_event(incoming, NOW)
    assert len(outs) == 1
    parsed = cot.parse_event(outs[0])
    assert parsed["dest_uids"] == ["p1"]
    assert parsed["chat"]["room_id"] == "p1"


def test_second_command_within_3s_ignored():
    bot = _bot()
    bot.handle_event(_pos(), NOW)
    assert len(bot.handle_event(_chat("!luz"), NOW)) == 1
    assert bot.handle_event(_chat("!equipo"), NOW + timedelta(seconds=2)) == []
    third = bot.handle_event(_chat("!equipo"), NOW + timedelta(seconds=3))
    assert len(third) == 1


def test_ignores_own_and_old_chats():
    bot = _bot()
    own = _chat("!luz", sender_uid="mando-bot", sender_callsign="Mando")
    assert bot.handle_event(own, NOW) == []
    old = _chat("!luz", when=NOW - timedelta(seconds=121))
    assert bot.handle_event(old, NOW) == []


def test_tick_identity_first_and_after_60s():
    bot = _bot()
    first = bot.tick(NOW)
    assert len(first) == 1
    assert cot.parse_event(first[0])["type"] == "a-f-G-E-S"
    assert bot.tick(NOW + timedelta(seconds=30)) == []
    again = bot.tick(NOW + timedelta(seconds=60))
    assert len(again) == 1
    assert cot.parse_event(again[0])["type"] == "a-f-G-E-S"


def test_lost_contact_message():
    bot = _bot(lost_after=300)
    bot.handle_event(_pos(when=NOW), NOW)
    outs = bot.tick(NOW + timedelta(seconds=301))
    alls = [
        o for o in outs
        if (cot.parse_event(o) or {}).get("chat")
        and cot.parse_event(o)["chat"]["room_id"] == "All Chat Rooms"
    ]
    assert len(alls) == 1
    text = cot.parse_event(alls[0])["chat"]["text"]
    assert text.startswith("⚠ Recon lleva 5 min sin reportar.")


def test_lost_contact_message_with_grid():
    grid = Grid(5.1650, -75.4960, 100, 9, 9)
    place = Place(name="Torre sur", lat=5.16110, lon=-75.49175, ring=None)
    bot = _bot(lost_after=300, grid=grid, places=[place])
    bot.handle_event(
        _pos(lat=5.16110, lon=-75.49175, when=NOW), NOW)
    outs = bot.tick(NOW + timedelta(seconds=301))
    alls = [
        o for o in outs
        if (cot.parse_event(o) or {}).get("chat")
        and cot.parse_event(o)["chat"]["room_id"] == "All Chat Rooms"
    ]
    assert len(alls) == 1
    text = cot.parse_event(alls[0])["chat"]["text"]
    assert text == (
        "⚠ Recon lleva 5 min sin reportar. "
        "Última posición: E5, en Torre sur."
    )


def test_lost_contact_message_with_grid_no_place():
    grid = Grid(5.1650, -75.4960, 100, 9, 9)
    bot = _bot(lost_after=300, grid=grid, places=[])
    bot.handle_event(
        _pos(lat=5.16110, lon=-75.49175, when=NOW), NOW)
    outs = bot.tick(NOW + timedelta(seconds=301))
    alls = [
        o for o in outs
        if (cot.parse_event(o) or {}).get("chat")
        and cot.parse_event(o)["chat"]["room_id"] == "All Chat Rooms"
    ]
    assert len(alls) == 1
    text = cot.parse_event(alls[0])["chat"]["text"]
    assert text == (
        "⚠ Recon lleva 5 min sin reportar. Última posición: E5."
    )
