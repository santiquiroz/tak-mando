from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone

from mando import tools
from mando.cot import all_chat_event, dm_event
from mando.layer import FOLDER_GAME, LayerError, _write_json_atomic, parse_iso_z
from mando.zones import Zone

BRAIN_GAP_S = 4.0
BRAIN_HOURLY_PER_USER = 40
BRAIN_HOURLY_GLOBAL = 300
BRAIN_TIMEOUT_S = 45.0
STATUS_EVERY_S = 10.0

_MANDO_RE = re.compile(r"(?i)^mando[,: ]")
_DANADA = "La capa de juego está dañada; avisa a un organizador."
_last_layer_error: str | None = None


def _log(*parts) -> None:
    print(datetime.now().strftime("%H:%M:%S"), *parts, flush=True)


def _log_layer_error(exc: Exception) -> None:
    global _last_layer_error
    msg = str(exc)
    if msg != _last_layer_error:
        _last_layer_error = msg
        _log(f"capa de juego dañada: {msg}")


def _iso(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).isoformat()


def _route_event(bot, route: tuple, text: str, now: datetime) -> str:
    kind, uid, callsign = route
    if kind == "dm":
        return dm_event(bot.uid, bot.callsign, uid, callsign, text, now)
    return all_chat_event(bot.uid, bot.callsign, text, now)


def _limited(bot, sender: str, now: datetime) -> bool:
    last = bot._last_ask.get(sender)
    if last is not None and (now - last).total_seconds() < BRAIN_GAP_S:
        return True
    cutoff = now - timedelta(hours=1)
    recent = [t for t in bot._ask_times.get(sender, []) if t > cutoff]
    bot._ask_times[sender] = recent
    if len(recent) >= BRAIN_HOURLY_PER_USER:
        return True
    bot._ask_all = [t for t in bot._ask_all if t > cutoff]
    return len(bot._ask_all) >= BRAIN_HOURLY_GLOBAL


def authorize_reply(bot, name, args, sender, chat, requester, now):
    if bot.layer is None:
        return bot._chat_reply(
            chat, sender, requester, "Este bot no tiene capa de juego.", now
        )
    try:
        allowed = bot.layer.authorized()
    except (LayerError, OSError):
        return bot._chat_reply(
            chat, sender, requester, _DANADA, now,
        )
    if sender not in allowed:
        return bot._chat_reply(
            chat, sender, requester,
            "Solo un autorizado puede autorizar.", now,
        )
    found = bot.roster.find(args)
    if found is None:
        return bot._chat_reply(
            chat, sender, requester,
            f"No veo a '{args}' conectado.", now,
        )
    who = chat.get("sender_callsign") or sender
    try:
        if name == "autorizar":
            bot.layer.authorize(found.uid)
        else:
            bot.layer.revoke(found.uid)
    except (LayerError, OSError):
        return bot._chat_reply(
            chat, sender, requester, _DANADA, now,
        )
    if name == "autorizar":
        bot.events.add(
            "mapa", f"{who} autorizó a {found.callsign}", now
        )
        return bot._chat_reply(
            chat, sender, requester, f"{found.callsign} autorizado.", now
        )
    bot.events.add(
        "mapa", f"{who} desautorizó a {found.callsign}", now
    )
    return bot._chat_reply(
        chat, sender, requester,
        f"{found.callsign} ya no está autorizado.", now,
    )


def confirm_reply(bot, chat, sender, matched, now, sun):
    requester = bot.roster.get(sender)
    callsign = chat.get("sender_callsign") or sender
    try:
        allowed = bot.layer.authorized()
    except (LayerError, OSError):
        return bot._chat_reply(chat, sender, requester, _DANADA, now)
    if sender not in allowed:
        return bot._chat_reply(
            chat, sender, requester,
            "Solo un autorizado puede confirmar propuestas.", now,
        )
    actor = tools.Actor(sender, callsign, authorized=True)
    tctx = tools.ToolContext(
        bot.layer, bot.events, bot._context(now, sun, requester),
        dem=bot.dem, exposure=bot.exposure, heights=bot.heights,
    )
    try:
        text = tools.execute(
            "confirmar_propuesta",
            {"numero": int(matched.group(2)),
             "aceptar": matched.group(1).lower() == "ok"},
            actor, tctx,
        )
    except (LayerError, OSError):
        return bot._chat_reply(chat, sender, requester, _DANADA, now)
    return bot._chat_reply(chat, sender, requester, text, now)


def ask_brain(bot, sender, callsign, text, route, now, sun):
    if sender in bot._pending:
        return "Sigo con tu mensaje anterior."
    if _limited(bot, sender, now):
        return "Dame un respiro, prueba en un minuto."
    try:
        if bot.layer is not None:
            authorized = sender in bot.layer.authorized()
        else:
            authorized = False
    except (LayerError, OSError):
        return _DANADA
    actor = tools.Actor(sender, callsign, authorized=authorized)
    requester = bot.roster.get(sender)
    tctx = tools.ToolContext(
        bot.layer, bot.events, bot._context(now, sun, requester),
        dem=bot.dem, exposure=bot.exposure, heights=bot.heights,
    )
    cancel = threading.Event()
    future = bot.executor.submit(bot.brain.answer, actor, text, tctx, now, cancel)
    bot._pending[sender] = (future, route, now, cancel)
    bot._last_ask[sender] = now
    bot._ask_times.setdefault(sender, []).append(now)
    bot._ask_all.append(now)
    return None


def brain_reply(bot, chat, sender, stripped, now, sun):
    callsign = chat.get("sender_callsign") or sender
    if (chat.get("room_id") or "") == bot.uid:
        route = ("dm", sender, callsign)
        body = stripped
    else:
        matched = _MANDO_RE.match(stripped)
        if matched is None:
            return None
        route = ("all", None, None)
        body = stripped[matched.end():].strip(",: ")
        if not body:
            return None
    text = ask_brain(bot, sender, callsign, body, route, now, sun)
    if text is None:
        return None
    return _route_event(bot, route, text, now)


def drain(bot, now):
    out = []
    for uid, entry in list(bot._pending.items()):
        future, route, started = entry[0], entry[1], entry[2]
        cancel = entry[3] if len(entry) > 3 else None
        if future.done():
            del bot._pending[uid]
            try:
                text = future.result()
            except Exception:
                text = "Sin cerebro ahora, usa !ayuda."
            out.append(_route_event(bot, route, text, now))
        elif (now - started).total_seconds() > BRAIN_TIMEOUT_S:
            del bot._pending[uid]
            if cancel is not None:
                cancel.set()
            out.append(_route_event(
                bot, route, "Sin cerebro ahora, usa !ayuda.", now
            ))
    return out


def due_announcements(bot, now):
    out = []
    if bot.layer is None:
        return out
    try:
        due = bot.layer.due_announcements(now)
    except (LayerError, OSError) as exc:
        _log_layer_error(exc)
        return out
    for item in due:
        text = item.get("text", "") if isinstance(item, dict) else ""
        audience = item.get("audience", "todos") if isinstance(item, dict) else "todos"
        if audience == "autorizados":
            try:
                allowed = bot.layer.authorized()
            except (LayerError, OSError) as exc:
                _log_layer_error(exc)
                return out
            for player in bot.roster.players():
                if player.uid in allowed:
                    out.append(dm_event(
                        bot.uid, bot.callsign, player.uid,
                        player.callsign, text, now,
                    ))
        else:
            out.append(all_chat_event(
                bot.uid, bot.callsign, text, now
            ))
        bot.events.add("aviso", text, now)
    return out


def notify_proposals(bot, now):
    out = []
    if bot.layer is None:
        return out
    try:
        allowed = bot.layer.authorized()
        props = bot.layer.proposals()
    except (LayerError, OSError) as exc:
        _log_layer_error(exc)
        return out
    targets = [p for p in bot.roster.players() if p.uid in allowed]
    if not targets:
        return out
    for prop in props:
        if not isinstance(prop, dict) or prop.get("notified"):
            continue
        text = tools.proposal_notice(prop)
        try:
            bot.layer.mark_notified(prop.get("n"))
        except (LayerError, OSError) as exc:
            _log_layer_error(exc)
            continue
        for player in targets:
            out.append(dm_event(
                bot.uid, bot.callsign, player.uid,
                player.callsign, text, now,
            ))
    return out


def expire_features(bot, now):
    removed = []
    if bot.layer is None:
        return removed
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        feats = bot.layer.features(FOLDER_GAME)
    except (LayerError, OSError) as exc:
        _log_layer_error(exc)
        return removed
    for feat in feats:
        props = feat.get("properties", {}) if isinstance(feat, dict) else {}
        exp = parse_iso_z(props.get("expires"))
        if exp is None or exp > now:
            continue
        if not props.get("id"):
            continue
        try:
            bot.layer.delete_feature(props.get("id"), "mando-bot")
        except (LayerError, OSError) as exc:
            _log_layer_error(exc)
            continue
        name = props.get("name", props.get("id"))
        bot.events.add("contacto", f"Contacto retirado: {name}", now)
        removed.append(name)
    return removed


def reload_hazards(bot):
    if bot.layer is None:
        return "unchanged"
    mtime = bot.layer.mtime()
    if mtime == bot._layer_mtime:
        return "unchanged"
    bot._layer_mtime = mtime
    try:
        peligros = []
        for feat in bot.layer.features(FOLDER_GAME):
            props = feat.get("properties", {})
            if props.get("kind") != "peligro":
                continue
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            if not coords or not coords[0]:
                continue
            peligros.append(Zone(
                name=props.get("name", ""),
                ring=coords[0],
                message=props.get("description") or "",
            ))
        bot.geofence.set_zones(bot.base_zones + peligros)
    except (LayerError, OSError):
        return "corrupt"
    return "ok"


def write_status(bot, now):
    if bot.status_path is None:
        return
    if (
        bot._last_status is not None
        and (now - bot._last_status).total_seconds() < STATUS_EVERY_S
    ):
        return
    bot._last_status = now
    players = []
    for p in bot.roster.players():
        players.append({
            "uid": p.uid, "callsign": p.callsign,
            "lat": p.lat, "lon": p.lon,
            "last_seen": _iso(p.last_seen),
        })
    try:
        _write_json_atomic(bot.status_path, {
            "updated": _iso(now),
            "players": players,
            "events": bot.events.to_list(),
        })
    except (LayerError, OSError) as exc:
        _log_layer_error(exc)
        return
