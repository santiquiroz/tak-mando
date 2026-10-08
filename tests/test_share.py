import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import pytest

from mando import cot
from mando.__main__ import build_parser
from mando.bot import Bot, shared_package
from mando.commands import Context, parse_command, run_command
from mando.weather import ForecastCache

NOW = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)


def _pkg_file(tmp_path, data=b"field-data"):
    path = tmp_path / "campo.zip"
    path.write_bytes(data)
    return path


def _package(tmp_path, name="Paquete del campo",
             url_base="https://tak.example.com:8443"):
    return shared_package(_pkg_file(tmp_path), name, url_base)


def _bot(package=None, **kw):
    params = dict(
        uid="mando-bot", callsign="Mando", lat=0.0, lon=0.0,
        utc_offset_h=-5, zones=[], places=[],
        forecast_cache=ForecastCache(lambda: []), announce=False,
        package=package,
    )
    params.update(kw)
    return Bot(**params)


def _pos(uid="p1", callsign="Recon", when=NOW):
    return {
        "uid": uid, "type": "a-f-G-U-C-I", "how": "h-g-i-g-o",
        "time": when, "stale": when + timedelta(hours=1),
        "lat": 0.005, "lon": 0.005, "callsign": callsign,
        "chat": None, "dest_uids": [],
    }


def _chat(text, sender_uid="p1", sender_callsign="Recon", when=NOW):
    return cot.parse_event(cot.chat_event(
        sender_uid, sender_callsign, "All Chat Rooms", "All Chat Rooms",
        text, when))


def _ctx():
    return Context(
        now_utc=NOW, utc_offset_h=-5, requester=None, players=[],
        places=[], zones=[], sun={}, moon=0.0, hours=[],
        forecast_age_s=None,
    )


def test_fileshare_event_shape():
    xml = cot.fileshare_event(
        "mando-bot", "Mando", "p1", "campo.zip", "Paquete del campo",
        "https://tak.example.com:8443/Marti/sync/content?hash=abc",
        1234, "abc", NOW, transfer_uid="t-1",
    )
    root = ElementTree.fromstring(xml)
    assert root.get("type") == "b-f-t-r"
    assert root.get("uid") == "t-1"
    assert root.get("how") == "h-e"
    assert root.get("time") == "2026-10-10T22:00:00.000Z"
    assert root.get("start") == root.get("time")
    assert root.get("stale") == "2026-10-10T22:10:00.000Z"
    point = root.find("point")
    assert (point.get("lat"), point.get("lon")) == ("0.0", "0.0")
    assert point.get("hae") == "9999999.0"
    assert point.get("ce") == "9999999.0"
    assert point.get("le") == "9999999.0"
    share = root.find("detail/fileshare")
    assert share.attrib == {
        "filename": "campo.zip",
        "senderUrl": "https://tak.example.com:8443/Marti/sync/content?hash=abc",
        "sizeInBytes": "1234",
        "sha256": "abc",
        "senderUid": "mando-bot",
        "senderCallsign": "Mando",
        "name": "Paquete del campo",
    }
    ack = root.find("detail/ackrequest")
    assert ack.get("uid") == root.get("uid")
    assert ack.get("ackrequested") == "true"
    assert ack.get("tag") == "Paquete del campo"
    assert root.find("detail/marti/dest").get("uid") == "p1"
    assert "<detail>" in xml and "</detail>" in xml
    assert "Detail" not in xml


def test_fileshare_event_default_transfer_uid_is_uuid():
    xml = cot.fileshare_event(
        "b", "M", "p1", "f.zip", "N", "https://h/u", 9, "s", NOW)
    uuid.UUID(ElementTree.fromstring(xml).get("uid"))


def test_fileshare_event_escapes_attributes():
    xml = cot.fileshare_event(
        "u&1", "A<B", "p1", "f\"f", "n'n",
        "https://h/?a=1&b=2", 5, "s", NOW, transfer_uid="t&<>\"'",
    )
    root = ElementTree.fromstring(xml)
    assert root.get("uid") == "t&<>\"'"
    share = root.find("detail/fileshare")
    assert share.get("filename") == "f\"f"
    assert share.get("senderUrl") == "https://h/?a=1&b=2"
    assert share.get("senderUid") == "u&1"
    assert share.get("senderCallsign") == "A<B"
    assert share.get("name") == "n'n"
    assert root.find("detail/ackrequest").get("uid") == "t&<>\"'"


def test_shared_package_computes_fields(tmp_path):
    data = b"field-data" * 100
    path = _pkg_file(tmp_path, data)
    pkg = shared_package(
        path, "Paquete del campo", "https://tak.example.com:8443/")
    assert pkg.filename == "campo.zip"
    assert pkg.name == "Paquete del campo"
    assert pkg.sha256 == hashlib.sha256(data).hexdigest()
    assert pkg.size_bytes == len(data)
    assert pkg.url == (
        "https://tak.example.com:8443/Marti/sync/content"
        f"?hash={pkg.sha256}"
    )


def test_shared_package_missing_file_raises(tmp_path):
    with pytest.raises(ValueError):
        shared_package(tmp_path / "nope.zip", "X", "https://h")


def test_first_position_sends_fileshare_and_dm_once(tmp_path):
    bot = _bot(package=_package(tmp_path))
    outs = bot.handle_event(_pos(), NOW)
    assert len(outs) == 2
    share = ElementTree.fromstring(outs[0])
    assert share.get("type") == "b-f-t-r"
    assert share.find("detail/marti/dest").get("uid") == "p1"
    assert "Detail" not in outs[0]
    dm = cot.parse_event(outs[1])
    assert dm["dest_uids"] == ["p1"]
    assert dm["chat"]["text"] == (
        "Te envié el paquete del campo \"Paquete del campo\" "
        "(mapas satelitales y capa táctica). Acéptalo en la "
        "notificación de ATAK/iTAK. Si no te llegó, escribe !mapas."
    )
    later = NOW + timedelta(seconds=10)
    assert bot.handle_event(_pos(when=later), later) == []


def test_sent_state_survives_restart(tmp_path):
    state = tmp_path / "sent.json"
    pkg = _package(tmp_path)
    bot1 = _bot(package=pkg, sent_state=state)
    assert len(bot1.handle_event(_pos(), NOW)) == 2
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved == {pkg.sha256: ["p1"]}
    bot2 = _bot(package=pkg, sent_state=state)
    later = NOW + timedelta(seconds=60)
    assert bot2.handle_event(_pos(when=later), later) == []


def test_sent_state_invalid_file_means_empty(tmp_path):
    state = tmp_path / "sent.json"
    state.write_text("not json{{{", encoding="utf-8")
    bot = _bot(package=_package(tmp_path), sent_state=state)
    assert len(bot.handle_event(_pos(), NOW)) == 2


def test_mapas_with_package(tmp_path):
    bot = _bot(package=_package(tmp_path))
    bot.handle_event(_pos(when=NOW - timedelta(seconds=30)),
                     NOW - timedelta(seconds=30))
    outs = bot.handle_event(_chat("!mapas"), NOW)
    assert len(outs) == 2
    share = ElementTree.fromstring(outs[0])
    assert share.get("type") == "b-f-t-r"
    assert share.find("detail/marti/dest").get("uid") == "p1"
    reply = cot.parse_event(outs[1])
    assert reply["chat"]["text"] == (
        "Paquete enviado: Paquete del campo. Acéptalo en la notificación."
    )


def test_mapas_by_dm_gets_dm_reply(tmp_path):
    bot = _bot(package=_package(tmp_path))
    bot.handle_event(_pos(when=NOW - timedelta(seconds=30)),
                     NOW - timedelta(seconds=30))
    incoming = cot.parse_event(cot.dm_event(
        "p1", "Recon", "mando-bot", "Mando", "!mapas", NOW))
    outs = bot.handle_event(incoming, NOW)
    assert len(outs) == 2
    reply = cot.parse_event(outs[1])
    assert reply["dest_uids"] == ["p1"]
    assert reply["chat"]["room_id"] == "p1"


def test_mapas_without_package():
    bot = _bot()
    bot.handle_event(_pos(when=NOW - timedelta(seconds=30)),
                     NOW - timedelta(seconds=30))
    outs = bot.handle_event(_chat("!mapas"), NOW)
    assert len(outs) == 1
    assert cot.parse_event(outs[0])["chat"]["text"] == (
        "Este servidor no tiene paquete de mapas configurado."
    )
    assert run_command("mapas", "", _ctx()) == (
        "Este servidor no tiene paquete de mapas configurado."
    )


def test_mapas_aliases():
    assert parse_command("!mapas")[0] == "mapas"
    assert parse_command("!mapa")[0] == "mapas"
    assert parse_command("!paquete")[0] == "mapas"


def test_share_cli_flags():
    args = build_parser().parse_args(["mando.zip", "--zones", "campo.geojson"])
    assert args.share_package is None
    assert args.share_name == "Paquete del campo"
    assert args.share_url_base is None
    assert args.share_state is None
    args = build_parser().parse_args([
        "mando.zip", "--zones", "campo.geojson",
        "--share-package", "campo.zip", "--share-name", "Campo",
        "--share-url-base", "https://h:8443", "--share-state", "s.json",
    ])
    assert (args.share_package, args.share_name,
            args.share_url_base, args.share_state) == (
        "campo.zip", "Campo", "https://h:8443", "s.json")


def test_player_without_gps_fix_still_gets_the_package(tmp_path):
    from datetime import datetime, timedelta, timezone

    from mando.bot import Bot, shared_package
    from mando.cot import parse_event

    zip_path = tmp_path / "campo.zip"
    zip_path.write_bytes(b"PK fake")
    now = datetime(2026, 10, 8, 13, 0, tzinfo=timezone.utc)
    bot = Bot(package=shared_package(zip_path, "Campo", "https://tak.example:8443"))
    no_fix = {"uid": "ANDROID-1", "type": "a-f-G-U-C", "callsign": "santi", "lat": 0.0, "lon": 0.0,
              "stale": now + timedelta(minutes=2), "chat": None, "dest_uids": []}
    sent = [parse_event(x) for x in bot.handle_event(no_fix, now)]
    assert [e["type"] for e in sent] == ["b-f-t-r", "b-t-f"]
    assert sent[0]["dest_uids"] == ["ANDROID-1"]
    assert bot.roster.players() == []
    assert bot.handle_event(no_fix, now + timedelta(seconds=5)) == []
