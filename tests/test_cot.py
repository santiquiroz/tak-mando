import uuid
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import pytest

from mando.cot import (
    all_chat_event,
    chat_event,
    cot_time,
    dm_event,
    identity_event,
    parse_event,
    split_stream,
)

NOW = datetime(2026, 10, 10, 21, 0, 0, tzinfo=timezone.utc)


def test_cot_time_milliseconds():
    assert cot_time(NOW) == "2026-10-10T21:00:00.000Z"


def test_cot_time_keeps_millis():
    assert cot_time(NOW.replace(microsecond=456789)) == "2026-10-10T21:00:00.456Z"


def test_identity_event_shape():
    root = ElementTree.fromstring(identity_event("mando-bot", "Mando", 5.1606, -75.4918, NOW))
    assert root.tag == "event"
    assert root.get("version") == "2.0"
    assert root.get("uid") == "mando-bot"
    assert root.get("type") == "a-f-G-E-S"
    assert root.get("how") == "h-g-i-g-o"
    assert root.get("time") == "2026-10-10T21:00:00.000Z"
    assert root.get("start") == root.get("time")
    assert root.get("stale") == "2026-10-10T21:05:00.000Z"
    point = root.find("point")
    assert point.get("lat") == "5.1606000"
    assert point.get("lon") == "-75.4918000"
    assert point.get("hae") == "9999999.0"
    assert point.get("ce") == "9999999.0"
    assert point.get("le") == "9999999.0"
    detail = root.find("detail")
    contact = detail.find("contact")
    assert contact.get("callsign") == "Mando"
    assert contact.get("endpoint") == "*:-1:stcp"
    group = detail.find("__group")
    assert (group.get("name"), group.get("role")) == ("Cyan", "HQ")
    takv = detail.find("takv")
    assert takv.get("device") == "tak-mando"
    assert takv.get("platform") == "tak-mando"
    assert takv.get("os") == "python"
    assert takv.get("version") == "0.1.0"
    assert detail.find("remarks").text == "Bot del servidor. Escribe !ayuda en el chat."


def test_chat_event_shape():
    root = ElementTree.fromstring(chat_event("u1", "Alice", "Team", "room1", "hola", NOW, message_id="m1"))
    assert root.get("type") == "b-t-f"
    assert root.get("uid") == "GeoChat.u1.room1.m1"
    assert root.get("stale") == "2026-10-10T22:00:00.000Z"
    point = root.find("point")
    assert (point.get("lat"), point.get("lon")) == ("0.0", "0.0")
    detail = root.find("detail")
    chel = detail.find("__chat")
    assert chel.get("parent") == "RootContactGroup"
    assert chel.get("groupOwner") == "false"
    assert chel.get("messageId") == "m1"
    assert chel.get("chatroom") == "Team"
    assert chel.get("id") == "room1"
    assert chel.get("senderCallsign") == "Alice"
    grp = chel.find("chatgrp")
    assert grp.get("uid0") == "u1"
    assert grp.get("uid1") == "room1"
    assert grp.get("id") == "room1"
    link = detail.find("link")
    assert link.get("uid") == "u1"
    assert link.get("type") == "a-f-G-U-C"
    assert link.get("relation") == "p-p"
    remarks = detail.find("remarks")
    assert remarks.get("source") == "BAO.F.ATAK.u1"
    assert remarks.get("to") == "room1"
    assert remarks.get("time") == "2026-10-10T21:00:00.000Z"
    assert remarks.text == "hola"
    assert detail.find("marti") is None


def test_chat_event_with_dest_has_marti():
    root = ElementTree.fromstring(
        chat_event("u1", "Alice", "Bob", "u2", "hola", NOW, dest_uid="u2", message_id="m1")
    )
    assert root.find("detail/marti/dest").get("uid") == "u2"


def test_chat_event_default_message_id_is_uuid():
    parsed = parse_event(chat_event("u1", "A", "R", "r", "hi", NOW))
    uuid.UUID(parsed["chat"]["message_id"])


def test_dm_event():
    root = ElementTree.fromstring(dm_event("u1", "Alice", "u2", "Bob", "hola", NOW, message_id="m1"))
    chel = root.find("detail/__chat")
    assert chel.get("chatroom") == "Bob"
    assert chel.get("id") == "u2"
    assert root.find("detail/marti/dest").get("uid") == "u2"


def test_all_chat_event():
    root = ElementTree.fromstring(all_chat_event("u1", "Alice", "hola", NOW, message_id="m1"))
    chel = root.find("detail/__chat")
    assert chel.get("chatroom") == "All Chat Rooms"
    assert chel.get("id") == "All Chat Rooms"
    assert root.find("detail/marti") is None


def test_special_chars_round_trip():
    text = "& < > \" '"
    parsed = parse_event(chat_event("u1", "Al&ice", "Te<am", "r1", text, NOW, message_id="m1"))
    assert parsed["chat"]["text"] == text
    assert parsed["chat"]["sender_callsign"] == "Al&ice"
    assert parsed["chat"]["room_name"] == "Te<am"
    parsed = parse_event(identity_event("u&1", "A<B>C\"D'E", 0.0, 0.0, NOW))
    assert parsed["uid"] == "u&1"
    assert parsed["callsign"] == "A<B>C\"D'E"


def test_parse_identity_fields():
    parsed = parse_event(identity_event("mando-bot", "Mando", 5.1606, -75.4918, NOW))
    assert parsed["uid"] == "mando-bot"
    assert parsed["type"] == "a-f-G-E-S"
    assert parsed["lat"] == pytest.approx(5.1606)
    assert parsed["lon"] == pytest.approx(-75.4918)
    assert parsed["callsign"] == "Mando"
    assert parsed["time"] == NOW
    assert parsed["stale"] == NOW + timedelta(seconds=300)
    assert parsed["chat"] is None
    assert parsed["dest_uids"] == []


def test_parse_chat_fields():
    xml = chat_event("u1", "Alice", "Team", "room1", "hola equipo", NOW, dest_uid="u2", message_id="mid-9")
    parsed = parse_event(xml)
    assert parsed["chat"] == {
        "room_name": "Team",
        "room_id": "room1",
        "sender_uid": "u1",
        "sender_callsign": "Alice",
        "text": "hola equipo",
        "message_id": "mid-9",
    }
    assert parsed["dest_uids"] == ["u2"]


def test_parse_chat_sender_uid_falls_back_to_link():
    xml = (
        '<event version="2.0" uid="x" type="b-t-f" how="h-g-i-g-o"'
        ' time="2026-10-10T21:00:00.000Z" start="2026-10-10T21:00:00.000Z"'
        ' stale="2026-10-10T22:00:00.000Z">'
        '<point lat="0.0" lon="0.0" hae="9999999.0" ce="9999999.0" le="9999999.0"/>'
        "<detail>"
        '<__chat parent="RootContactGroup" groupOwner="false" messageId="m"'
        ' chatroom="R" id="r" senderCallsign="A"/>'
        '<link uid="fallback-uid" type="a-f-G-U-C" relation="p-p"/>'
        '<remarks source="s" to="r" time="2026-10-10T21:00:00.000Z">hi</remarks>'
        "</detail></event>"
    )
    assert parse_event(xml)["chat"]["sender_uid"] == "fallback-uid"


def test_parse_dm():
    parsed = parse_event(dm_event("u1", "Alice", "u2", "Bob", "hola", NOW, message_id="m1"))
    assert parsed["chat"]["room_id"] == "u2"
    assert parsed["chat"]["room_name"] == "Bob"
    assert parsed["dest_uids"] == ["u2"]


@pytest.mark.parametrize("bad", ["", "not xml", "<event><point", "<event version="])
def test_parse_broken_xml_returns_none(bad):
    assert parse_event(bad) is None


def test_parse_rejects_doctype():
    assert parse_event('<?xml version="1.0"?><!DOCTYPE foo><event uid="x"/>') is None


def test_parse_rejects_entity():
    assert parse_event('<event uid="x"><!ENTITY y "z"></event>') is None


def test_parse_time_with_and_without_fraction():
    with_fraction = parse_event(identity_event("a", "A", 0.0, 0.0, NOW))
    assert with_fraction["time"] == NOW
    xml = identity_event("a", "A", 0.0, 0.0, NOW).replace(".000Z", "Z")
    without_fraction = parse_event(xml)
    assert without_fraction["time"] == NOW
    assert without_fraction["stale"] == NOW + timedelta(seconds=300)


def test_split_stream_complete_and_partial():
    e1 = identity_event("a", "A", 0.0, 0.0, NOW)
    e2 = identity_event("b", "B", 1.0, 1.0, NOW)
    partial = '<event version="2.0" uid="c"'
    events, rest = split_stream(e1 + e2 + partial)
    assert events == [e1, e2]
    assert rest == partial


def test_split_stream_drops_junk_before_first_event():
    e1 = identity_event("a", "A", 0.0, 0.0, NOW)
    events, rest = split_stream('<?xml version="1.0"?>\nxxx' + e1)
    assert events == [e1]
    assert rest == ""


def test_split_stream_huge_remainder_returns_empty():
    events, rest = split_stream("<event " + "x" * 1000000)
    assert events == []
    assert rest == ""


def test_split_stream_keeps_remainder_at_limit():
    buf = "<event " + "x" * (1000000 - len("<event "))
    events, rest = split_stream(buf)
    assert events == []
    assert rest == buf


def test_sent_events_use_lowercase_detail_element():
    from datetime import datetime, timezone

    from mando.cot import all_chat_event, dm_event, identity_event

    now = datetime(2026, 10, 10, 21, 0, tzinfo=timezone.utc)
    events = [
        identity_event("bot", "Mando", 5.16, -75.49, now),
        dm_event("bot", "Mando", "u1", "Recon", "hola", now),
        all_chat_event("bot", "Mando", "hola", now),
    ]
    for event in events:
        assert "<detail>" in event and "</detail>" in event
        assert "Detail" not in event
