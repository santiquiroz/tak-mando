import uuid
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

_START = "<event"
_END = "</event>"
_MAX_REMAINDER = 1000000


def cot_time(dt):
    if dt.tzinfo is None:
        utc = dt.replace(tzinfo=timezone.utc)
    else:
        utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S") + f".{utc.microsecond // 1000:03d}Z"


def xml_escape(text):
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def identity_event(uid, callsign, lat, lon, now, stale_s=300, version="0.1.0"):
    t = cot_time(now)
    stale = cot_time(now + timedelta(seconds=stale_s))
    return (
        f'<event version="2.0" uid="{xml_escape(uid)}" type="a-f-G-E-S"'
        f' how="h-g-i-g-o" time="{t}" start="{t}" stale="{stale}">'
        f'<point lat="{lat:.7f}" lon="{lon:.7f}" hae="9999999.0" ce="9999999.0" le="9999999.0"/>'
        "<detail>"
        f'<contact callsign="{xml_escape(callsign)}" endpoint="*:-1:stcp"/>'
        '<__group name="Cyan" role="HQ"/>'
        f'<takv device="tak-mando" platform="tak-mando" os="python" version="{xml_escape(version)}"/>'
        "<remarks>Bot del servidor. Escribe !ayuda en el chat.</remarks>"
        "</detail>"
        "</event>"
    )


def chat_event(sender_uid, sender_callsign, room_name, room_id, text, now, dest_uid=None, message_id=None):
    mid = message_id if message_id is not None else str(uuid.uuid4())
    t = cot_time(now)
    stale = cot_time(now + timedelta(seconds=3600))
    if dest_uid is None:
        marti = ""
    else:
        marti = f'<marti><dest uid="{xml_escape(dest_uid)}"/></marti>'
    return (
        f'<event version="2.0" uid="{xml_escape(f"GeoChat.{sender_uid}.{room_id}.{mid}")}" type="b-t-f"'
        f' how="h-g-i-g-o" time="{t}" start="{t}" stale="{stale}">'
        '<point lat="0.0" lon="0.0" hae="9999999.0" ce="9999999.0" le="9999999.0"/>'
        "<detail>"
        f'<__chat parent="RootContactGroup" groupOwner="false" messageId="{xml_escape(mid)}"'
        f' chatroom="{xml_escape(room_name)}" id="{xml_escape(room_id)}"'
        f' senderCallsign="{xml_escape(sender_callsign)}">'
        f'<chatgrp uid0="{xml_escape(sender_uid)}" uid1="{xml_escape(room_id)}" id="{xml_escape(room_id)}"/>'
        "</__chat>"
        f'<link uid="{xml_escape(sender_uid)}" type="a-f-G-U-C" relation="p-p"/>'
        f'<remarks source="BAO.F.ATAK.{xml_escape(sender_uid)}" to="{xml_escape(room_id)}"'
        f' time="{t}">{xml_escape(text)}</remarks>'
        f"{marti}</detail>"
        "</event>"
    )


def dm_event(sender_uid, sender_callsign, to_uid, to_callsign, text, now, message_id=None):
    return chat_event(
        sender_uid, sender_callsign, to_callsign, to_uid, text, now,
        dest_uid=to_uid, message_id=message_id,
    )


def all_chat_event(sender_uid, sender_callsign, text, now, message_id=None):
    return chat_event(
        sender_uid, sender_callsign, "All Chat Rooms", "All Chat Rooms", text, now,
        message_id=message_id,
    )


def _parse_cot_time(value):
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_event(xml):
    # Reject entity declarations before parsing: the parser would expand them.
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        return None
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None
    if root.tag != "event":
        return None
    detail = root.find("detail")
    point = root.find("point")
    lat = _parse_float(point.get("lat")) if point is not None else None
    lon = _parse_float(point.get("lon")) if point is not None else None
    callsign = None
    chat = None
    dest_uids = []
    if detail is not None:
        contact = detail.find("contact")
        if contact is not None:
            callsign = contact.get("callsign")
        chel = detail.find("__chat")
        remarks = detail.find("remarks")
        if chel is not None and remarks is not None and remarks.text is not None:
            sender_uid = None
            grp = chel.find("chatgrp")
            if grp is not None:
                sender_uid = grp.get("uid0")
            if not sender_uid:
                link = detail.find("link")
                if link is not None:
                    sender_uid = link.get("uid")
            chat = {
                "room_name": chel.get("chatroom", ""),
                "room_id": chel.get("id", ""),
                "sender_uid": sender_uid or "",
                "sender_callsign": chel.get("senderCallsign", ""),
                "text": remarks.text,
                "message_id": chel.get("messageId", ""),
            }
        for dest in detail.findall("marti/dest"):
            uid = dest.get("uid")
            if uid is not None:
                dest_uids.append(uid)
    return {
        "uid": root.get("uid", ""),
        "type": root.get("type", ""),
        "how": root.get("how", ""),
        "time": _parse_cot_time(root.get("time")),
        "stale": _parse_cot_time(root.get("stale")),
        "lat": lat,
        "lon": lon,
        "callsign": callsign,
        "chat": chat,
        "dest_uids": dest_uids,
    }


def split_stream(buffer):
    events = []
    rest = buffer
    while True:
        start = rest.find(_START)
        if start < 0:
            return events, ""
        end = rest.find(_END, start)
        if end < 0:
            remainder = rest[start:]
            # A peer that never closes the event must not exhaust memory.
            if len(remainder) > _MAX_REMAINDER:
                return events, ""
            return events, remainder
        events.append(rest[start:end + len(_END)])
        rest = rest[end + len(_END):]
