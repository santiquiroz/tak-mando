from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import timedelta

from mando.geo import bearing_deg, cardinal_es, centroid, describe_offset, distance_to_ring_m, format_distance
from mando.weather import next_hours
from mando.zones import describe_location

_ALIASES = {
    "ayuda": "ayuda",
    "help": "ayuda",
    "h": "ayuda",
    "luz": "luz",
    "sol": "luz",
    "clima": "clima",
    "tiempo": "clima",
    "equipo": "equipo",
    "team": "equipo",
    "donde": "donde",
    "ubicar": "donde",
    "peligros": "peligros",
    "peligro": "peligros",
}

_AYUDA = (
    "Comandos: !luz (sol y oscuridad) \u00b7 !clima (pr\u00f3ximas 3 h) \u00b7 "
    "!equipo (d\u00f3nde est\u00e1 cada uno) \u00b7 !donde <callsign> \u00b7 !peligros (cerca de ti)"
)
_DESCONOCIDO = "No conozco ese comando. Escribe !ayuda."
_SIN_PRONOSTICO = "Sin pron\u00f3stico: el servidor no pudo consultarlo."
_SIN_EQUIPO = "No hay nadie m\u00e1s reportando posici\u00f3n."
_SIN_PELIGRO = "Ning\u00fan peligro marcado a menos de 200 m."
_SIN_POSICION = "No tengo tu posici\u00f3n todav\u00eda."
_USO_DONDE = "Uso: !donde <callsign>"
_SIN_SOL = "No hay datos solares para hoy."


@dataclass
class Context:
    now_utc: object
    utc_offset_h: float
    requester: object
    players: list
    places: list
    zones: list
    sun: dict
    moon: float
    hours: list
    forecast_age_s: object


def _strip_accents(text):
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def parse_command(text):
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped.startswith("!"):
        return None
    content = stripped[1:].strip()
    if not content:
        return ("desconocido", "")
    head, _, tail = content.partition(" ")
    name = _strip_accents(head.lower())
    return (_ALIASES.get(name, "desconocido"), tail.strip()[:40])


def _age(last_seen, now_utc):
    secs = max(0.0, (now_utc - last_seen).total_seconds())
    if secs < 60:
        return f"{int(secs)} s"
    if secs < 3600:
        return f"{int(secs // 60)} min"
    return f"{int(secs // 3600)} h"


def _duration(delta):
    total_m = int(max(0.0, delta.total_seconds()) // 60)
    hours, mins = divmod(total_m, 60)
    if hours > 0:
        if mins > 0:
            return f"{hours} h {mins} min"
        return f"{hours} h"
    return f"{mins} min"


def _has_pos(player):
    return player is not None and player.lat is not None and player.lon is not None


def _find_player(players, query):
    q = query.lower()
    for p in players:
        if p.callsign.lower() == q:
            return p
    matches = [p for p in players if p.callsign.lower().startswith(q)]
    if len(matches) == 1:
        return matches[0]
    return None


def _luz(ctx):
    now_local = (ctx.now_utc + timedelta(hours=ctx.utc_offset_h)).replace(tzinfo=None)
    sun = ctx.sun or {}
    sunset = sun.get("sunset")
    nautical = sun.get("nautical_dusk")
    sunrise = sun.get("sunrise")
    moon = f"{ctx.moon * 100:.0f} %"
    if sunset is None and sunrise is None and nautical is None:
        return _SIN_SOL
    if sunrise is not None and now_local < sunrise:
        return (
            f"Es de noche. Amanece {sunrise:%H:%M} "
            f"(en {_duration(sunrise - now_local)}). Luna {moon}."
        )
    if sunset is not None and now_local < sunset:
        span = _duration(sunset - now_local)
        if nautical is not None:
            return (
                f"Sol se pone {sunset:%H:%M} (en {span}). "
                f"Oscuridad total {nautical:%H:%M}. Luna {moon}."
            )
        return f"Sol se pone {sunset:%H:%M} (en {span}). Luna {moon}."
    if sunset is not None and nautical is not None and now_local < nautical:
        return (
            f"Crep\u00fasculo. Oscuridad total {nautical:%H:%M} "
            f"(en {_duration(nautical - now_local)})."
        )
    if sunrise is not None:
        target = sunrise
        if target <= now_local:
            target = target + timedelta(days=1)
        return (
            f"Es de noche. Amanece {sunrise:%H:%M} "
            f"(en {_duration(target - now_local)}). Luna {moon}."
        )
    return f"Es de noche. Luna {moon}."


def _clima(ctx):
    upcoming = next_hours(ctx.hours, ctx.now_utc, 3)
    if not upcoming:
        return _SIN_PRONOSTICO
    parts = []
    for h in upcoming:
        local = h.time + timedelta(hours=ctx.utc_offset_h)
        temp = f"{h.temp_c:.0f}\u00b0C" if h.temp_c is not None else "?\u00b0C"
        prob = f"{h.rain_prob}%" if h.rain_prob is not None else "?%"
        mm = f"{h.rain_mm:.1f} mm" if h.rain_mm is not None else "? mm"
        parts.append(f"{local:%H:%M} {temp} lluvia {prob} {mm}")
    reply = " \u00b7 ".join(parts)
    if ctx.forecast_age_s is not None:
        reply += f" (pron\u00f3stico de hace {int(ctx.forecast_age_s // 60)} min)"
    return reply


def _equipo(ctx):
    req = ctx.requester
    others = [p for p in ctx.players if req is None or p.uid != req.uid][:10]
    if not others:
        return _SIN_EQUIPO
    out = []
    for p in others:
        age = _age(p.last_seen, ctx.now_utc)
        if _has_pos(req):
            out.append(f"{p.callsign} {describe_offset(req.lat, req.lon, p.lat, p.lon)} (hace {age})")
        else:
            out.append(f"{p.callsign} (hace {age})")
    return "\n".join(out)


def _donde(args, ctx):
    query = args.strip()
    if not query:
        return _USO_DONDE
    found = _find_player(ctx.players, query)
    if found is None:
        callsigns = ", ".join(p.callsign for p in ctx.players)
        return f"No encuentro a '{query}'. Conectados: {callsigns}"
    age = _age(found.last_seen, ctx.now_utc)
    loc = describe_location(ctx.places, found.lat, found.lon)
    if _has_pos(ctx.requester):
        offset = describe_offset(ctx.requester.lat, ctx.requester.lon, found.lat, found.lon)
        if loc:
            return f"{found.callsign}: {offset}, {loc} (hace {age})"
        return f"{found.callsign}: {offset} (hace {age})"
    if loc:
        return f"{found.callsign}: {loc} (hace {age})"
    return f"{found.callsign} (hace {age})"


def _peligros(ctx):
    req = ctx.requester
    if not _has_pos(req):
        return _SIN_POSICION
    near = []
    for zone in ctx.zones:
        dist = distance_to_ring_m(req.lat, req.lon, zone.ring)
        if dist <= 200:
            near.append((dist, zone))
    if not near:
        return _SIN_PELIGRO
    near.sort(key=lambda item: item[0])
    parts = []
    for dist, zone in near:
        if dist == 0:
            parts.append(f"{zone.name}: EST\u00c1S DENTRO")
        else:
            # Large zones (a whole slope) have their centre far away: report the edge distance, point at the centre.
            clat, clon = centroid(zone.ring)
            heading = cardinal_es(bearing_deg(req.lat, req.lon, clat, clon))
            parts.append(f"{zone.name} a {format_distance(dist)} al {heading}")
    return " \u00b7 ".join(parts)


def run_command(name, args, ctx):
    if name == "ayuda":
        reply = _AYUDA
    elif name == "luz":
        reply = _luz(ctx)
    elif name == "clima":
        reply = _clima(ctx)
    elif name == "equipo":
        reply = _equipo(ctx)
    elif name == "donde":
        reply = _donde(args, ctx)
    elif name == "peligros":
        reply = _peligros(ctx)
    else:
        reply = _DESCONOCIDO
    if len(reply) > 700:
        reply = reply[:699] + "\u2026"
    return reply
