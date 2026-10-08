"""Map tools with permissions and proposals for the LLM brain."""

import json
import unicodedata
from dataclasses import dataclass
from datetime import timedelta, timezone

from mando.commands import Context, run_command
from mando.elevation import line_of_sight
from mando.events import EventLog
from mando.exposure import cell_of, covered_route, exposed_fraction
from mando.geo import centroid, format_distance, haversine_m, point_in_ring
from mando.grid import grid_ref
from mando.layer import COLORS, FOLDER_GAME, FOLDER_PROPOSALS, Layer, LayerError, circle, parse_iso_z
from mando.places import resolve_place


def _fn(name, description, properties, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required)},
        },
    }


_S = {"type": "string"}
_LUGAR = {"type": "string", "description": "Cuadro (E5), edificio (12 o torre sur), callsign, 'aquí' o 'lat,lon'"}

TOOLS = [
    _fn("donde_esta", "Dónde está un jugador del equipo.", {"callsign": _S}, ["callsign"]),
    _fn("que_hay_en", "Qué hay en un lugar: edificios, peligros y objetos del mapa de juego.", {"lugar": _LUGAR}, ["lugar"]),
    _fn("estado_equipo", "Lista de jugadores con su cuadro y hace cuánto reportaron.", {}),
    _fn("peligros", "Zonas de peligro del campo y de la capa de juego.", {}),
    _fn("luz_y_clima", "Sol, oscuridad, luna y pronóstico de las próximas horas.", {}),
    _fn("sitrep", "Resumen de lo que pasó en los últimos minutos.", {"minutos": {"type": "integer", "minimum": 1, "maximum": 60}}),
    _fn("capa_juego", "Objetos de la capa de juego (con id) y propuestas pendientes.", {}),
    _fn("linea_de_vista", "Dice si hay línea de vista entre dos puntos (necesita DTED).", {
        "desde": _LUGAR, "hasta": _LUGAR,
    }, ["desde", "hasta"]),
    _fn("marcar_punto", "Pone un punto en el mapa de todos.", {
        "nombre": _S, "lugar": _LUGAR,
        "tipo": {"type": "string", "enum": ["objetivo", "peligro", "reunion", "medico", "spawn", "enemigo", "aliado", "desconocido", "info"]},
        "nota": _S,
    }, ["nombre", "lugar", "tipo"]),
    _fn("dibujar_zona", "Dibuja un círculo en el mapa de todos.", {
        "nombre": _S, "lugar": _LUGAR,
        "radio_m": {"type": "integer", "minimum": 10, "maximum": 300},
        "kind": {"type": "string", "enum": ["zona", "peligro", "objetivo"]},
        "nota": _S,
    }, ["nombre", "lugar", "radio_m", "kind"]),
    _fn("estado_objetivo", "Cambia el estado de un objetivo.", {
        "objetivo": {"type": "string", "description": "id (j-3) o nombre"},
        "estado": {"type": "string", "enum": ["libre", "nuestro", "enemigo", "disputado"]},
    }, ["objetivo", "estado"]),
    _fn("mover", "Mueve un objeto de la capa de juego.", {"objeto": _S, "lugar": _LUGAR}, ["objeto", "lugar"]),
    _fn("borrar", "Borra un objeto de la capa de juego.", {"objeto": _S}, ["objeto"]),
    _fn("reportar_contacto", "Publica un contacto que se borra solo en 10 minutos.", {
        "tipo": {"type": "string", "enum": ["infanteria", "vehiculo", "dron", "francotirador", "desconocido"]},
        "cantidad": {"type": "integer", "minimum": 1, "maximum": 50},
        "lugar": _LUGAR, "nota": _S,
    }, ["tipo", "lugar"]),
    _fn("ruta_cubierta", "Traza una ruta poco visible entre dos puntos (necesita mapa de visibilidad).", {
        "desde": _LUGAR, "hasta": _LUGAR,
    }, ["desde", "hasta"]),
    _fn("deshacer", "Deshace tu último cambio en el mapa.", {}),
    _fn("programar_aviso", "Programa un mensaje para dentro de N minutos.", {
        "minutos": {"type": "integer", "minimum": 1, "maximum": 240}, "texto": _S,
        "para": {"type": "string", "enum": ["todos", "autorizados"]},
    }, ["minutos", "texto", "para"]),
    _fn("anunciar", "Manda un mensaje a todo el equipo ahora.", {"texto": _S}, ["texto"]),
    _fn("confirmar_propuesta", "Acepta o descarta una propuesta pendiente.", {
        "numero": {"type": "integer"}, "aceptar": {"type": "boolean"},
    }, ["numero", "aceptar"]),
]
WRITE_TOOLS = frozenset({"marcar_punto", "dibujar_zona", "estado_objetivo", "mover", "borrar", "ruta_cubierta"})

_TIPOS = ("objetivo", "peligro", "reunion", "medico", "spawn", "enemigo", "aliado", "desconocido", "info")
_CONTACTS = ("infanteria", "vehiculo", "dron", "francotirador", "desconocido")
_CONTACT_LABEL = {"infanteria": "infantería", "vehiculo": "vehículo", "dron": "dron",
                  "francotirador": "francotirador", "desconocido": "desconocido"}
_CONTACT_COT = {"infanteria": "a-h-G-U-C-I", "vehiculo": "a-h-G-E-V", "dron": "a-h-A-M-F-Q",
                "francotirador": "a-h-G-U-C-I", "desconocido": "a-u-G"}
_SYMBOL_COT = {"enemigo": "a-h-G-U-C-I", "aliado": "a-f-G-U-C-I", "desconocido": "a-u-G"}
_SYMBOL_COLOR = {"aliado": "#34c759", "desconocido": "#ffcc00"}
_ZONAS = ("zona", "peligro", "objetivo")
_ESTADOS = ("libre", "nuestro", "enemigo", "disputado")
_PARA = ("todos", "autorizados")
_REF_LIMIT = 100
_LIMITE = "Límite de cambios por mensaje alcanzado."
_DANADA = "La capa de juego está dañada; avisa a un organizador."


@dataclass
class Actor:
    uid: str
    callsign: str
    authorized: bool
    is_mcp: bool = False


@dataclass
class ToolContext:
    layer: Layer
    events: EventLog
    command_context: Context
    writes_left: int = 5
    dem: object = None
    exposure: object = None
    heights: object = None


class _Missing(Exception):
    def __init__(self, campo):
        self.campo = campo


class _Range(Exception):
    def __init__(self, campo, a, b):
        self.campo = campo
        self.a = a
        self.b = b


class _BadEnum(Exception):
    def __init__(self, campo, valor):
        self.campo = campo
        self.valor = valor


def clean(text, limit):
    plain = "".join(c for c in str(text or "") if unicodedata.category(c)[0] != "C")
    return " ".join(plain.split())[:limit]


def proposal_notice(p):
    if not isinstance(p, dict):
        p = {}
    author = p.get("author", "?")
    summary = p.get("summary", "?")
    n = p.get("n", "?")
    return f"{author} propone: {summary}. #{n} → responde ok {n} o no {n}"


def load_heights(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    feats = data.get("features") if isinstance(data, dict) else None
    out = {}
    for feat in feats if isinstance(feats, list) else []:
        props = feat.get("properties") if isinstance(feat, dict) else None
        if not isinstance(props, dict):
            continue
        name = props.get("name")
        height = props.get("height_m")
        if not isinstance(name, str) or name.strip() == "":
            continue
        if isinstance(height, bool) or not isinstance(height, (int, float)):
            continue
        out[name] = float(height)
    return out


def execute(name, args, actor, ctx):
    try:
        fn = _DISPATCH.get(name)
        if fn is None:
            return f"Herramienta desconocida: {name}."
        data = args if isinstance(args, dict) else {}
        return fn(data, actor, ctx)[:600]
    except _Missing as err:
        return f"Faltan datos: {err.campo}."
    except _Range as err:
        return f"{err.campo} debe estar entre {err.a} y {err.b}."
    except _BadEnum as err:
        return f"{err.campo} no válido: {err.valor}."
    except LayerError:
        return _DANADA
    except Exception:
        return f"Error interno en {name}."


def _req_str(args, campo, limit):
    raw = args.get(campo)
    if not isinstance(raw, str) or clean(raw, limit) == "":
        raise _Missing(campo)
    return clean(raw, limit)


def _opt_str(args, campo, limit):
    raw = args.get(campo)
    if not isinstance(raw, str):
        return ""
    return clean(raw, limit)


def _req_int(args, campo):
    raw = args.get(campo)
    if raw is None or isinstance(raw, bool) or not isinstance(raw, int):
        raise _Missing(campo)
    return raw


def _req_int_range(args, campo, a, b):
    if campo not in args or args.get(campo) is None:
        raise _Missing(campo)
    raw = args.get(campo)
    if isinstance(raw, bool) or not isinstance(raw, int) or not a <= raw <= b:
        raise _Range(campo, a, b)
    return raw


def _opt_int(args, campo, default, a, b):
    if campo not in args or args.get(campo) is None:
        return default
    raw = args.get(campo)
    if isinstance(raw, bool) or not isinstance(raw, int) or not a <= raw <= b:
        raise _Range(campo, a, b)
    return raw


def _req_enum(args, campo, allowed):
    raw = args.get(campo)
    if raw is None or campo not in args:
        raise _Missing(campo)
    if isinstance(raw, str):
        raw = clean(raw, 20)
    if raw not in allowed:
        raise _BadEnum(campo, raw)
    return raw


def _req_bool(args, campo):
    if campo not in args or args.get(campo) is None:
        raise _Missing(campo)
    raw = args.get(campo)
    if not isinstance(raw, bool):
        raise _Missing(campo)
    return raw


def _now(ctx):
    return ctx.command_context.now_utc


def _can(actor):
    return bool(actor.authorized or actor.is_mcp)


def _take_write(ctx):
    if ctx.writes_left <= 0:
        return False
    ctx.writes_left -= 1
    return True


def _resolve(ctx, lugar):
    cc = ctx.command_context
    return resolve_place(lugar, grid=cc.grid, places=cc.places, players=cc.players, requester=cc.requester)


def _match(layer, ref):
    found = layer.matches(ref, FOLDER_GAME)
    if len(found) == 1:
        return found[0], None
    if not found:
        return None, f"No encuentro '{ref}' en la capa de juego."
    names = ", ".join(_fid_name(f) for f in found)
    return None, f"Hay varios que coinciden con '{ref}': {names}. Usa el id."


def _fid_name(feat):
    props = feat.get("properties", {})
    return f"{props.get('id', '?')} {props.get('name', '?')}"


def _desc(props):
    if props.get("kind") == "objetivo":
        status = props.get("status")
        return f"objetivo {status}" if status else "objetivo"
    return props.get("tipo") or props.get("kind", "?")


def _local_hhmm(at, offset_h):
    return (at + timedelta(hours=offset_h)).strftime("%H:%M")


def _iso_z(at):
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _donde_esta(args, actor, ctx):
    callsign = _req_str(args, "callsign", _REF_LIMIT)
    return run_command("donde", callsign, ctx.command_context)


def _estado_equipo(args, actor, ctx):
    return run_command("equipo", "", ctx.command_context)


def _luz_y_clima(args, actor, ctx):
    cc = ctx.command_context
    return run_command("luz", "", cc) + " " + run_command("clima", "", cc)


def _peligros(args, actor, ctx):
    base = run_command("peligros", "", ctx.command_context)
    names = []
    for feat in ctx.layer.features(FOLDER_GAME):
        props = feat.get("properties", {})
        if (props.get("kind") == "peligro" or props.get("tipo") == "peligro") and props.get("name"):
            names.append(props["name"])
    if not names:
        return base
    return base + " Capa de juego: " + ", ".join(names)


def _sitrep(args, actor, ctx):
    minutes = _opt_int(args, "minutos", 10, 1, 60)
    events = ctx.events.since(_now(ctx), minutes)
    if not events:
        return f"Sin novedades en los últimos {minutes} min."
    offset = ctx.command_context.utc_offset_h
    return " · ".join(f"{_local_hhmm(e.at, offset)} {e.text}" for e in events)


def _que_hay_en(args, actor, ctx):
    lugar = _req_str(args, "lugar", _REF_LIMIT)
    found = _resolve(ctx, lugar)
    if isinstance(found, str):
        return found
    cc = ctx.command_context
    ref = grid_ref(cc.grid, found.lat, found.lon) if cc.grid else None
    head = ref or found.label
    parts = []
    near = _near_places(cc, found, ref)
    if near:
        parts.append(", ".join(near))
    zones = [z.name for z in cc.zones if point_in_ring(found.lat, found.lon, z.ring)]
    if zones:
        parts.append("Peligro: " + ", ".join(zones))
    game = _near_game(ctx, found)
    if game:
        parts.append("Juego: " + ", ".join(game))
    if not parts:
        return f"{head}: nada marcado."
    return f"{head}: " + ". ".join(parts) + "."


def _near_places(cc, found, ref):
    best = {}
    for place in cc.places or []:
        dist = haversine_m(found.lat, found.lon, place.lat, place.lon)
        same = ref is not None and cc.grid is not None
        same = same and grid_ref(cc.grid, place.lat, place.lon) == ref
        if dist <= 60 or same:
            if place.name not in best or dist < best[place.name]:
                best[place.name] = dist
    return [f"{name} ({best[name]:.0f} m)" for name in best]


def _near_game(ctx, found):
    out = []
    for feat in ctx.layer.features(FOLDER_GAME):
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        if geom.get("type") == "Point":
            lon, lat = geom.get("coordinates", [None, None])[:2]
            if lat is None:
                continue
            dist = haversine_m(found.lat, found.lon, lat, lon)
            if dist <= 60:
                out.append(f"{props.get('id', '?')} {props.get('name', '?')} ({_desc(props)}) a {dist:.0f} m")
        elif geom.get("type") == "Polygon":
            rings = geom.get("coordinates", [])
            if rings and point_in_ring(found.lat, found.lon, rings[0]):
                out.append(f"{props.get('id', '?')} {props.get('name', '?')} ({_desc(props)})")
    return out


def _feat_ref(grid, feat):
    if grid is None:
        return None
    geom = feat.get("geometry", {})
    if geom.get("type") == "Point":
        coords = geom.get("coordinates", [None, None])[:2]
        if coords[1] is None:
            return None
        return grid_ref(grid, coords[1], coords[0])
    rings = geom.get("coordinates", [])
    if geom.get("type") != "Polygon" or not rings or not rings[0]:
        return None
    lat, lon = centroid(rings[0])
    return grid_ref(grid, lat, lon)


def _capa_juego(args, actor, ctx):
    grid = ctx.command_context.grid
    items = []
    for feat in ctx.layer.features(FOLDER_GAME):
        props = feat.get("properties", {})
        ref = _feat_ref(grid, feat)
        label = f"{props.get('id', '?')} {props.get('name', '?')} ({_desc(props)}"
        items.append(label + f", {ref})" if ref else label + ")")
    pending = [f"#{p['n']} {p['author']}: {p['summary']}" for p in ctx.layer.proposals()]
    if not items:
        text = "La capa de juego está vacía."
        if pending:
            text += " Propuestas: " + ", ".join(pending)
        return text
    text = " · ".join(items)
    if pending:
        text += ". Propuestas: " + ", ".join(pending)
    return text


def _point_color(tipo):
    if tipo == "objetivo":
        return COLORS["objetivo:libre"]
    if tipo in _SYMBOL_COLOR:
        return _SYMBOL_COLOR[tipo]
    return COLORS[tipo]


def _point_props(nombre, kind, tipo, nota, author, color, folder):
    props = {"name": nombre, "folder": folder, "kind": kind, "tipo": tipo,
             "description": nota, "author": author, "marker-color": color}
    if tipo in _SYMBOL_COT:
        props["cot_type"] = _SYMBOL_COT[tipo]
    if kind == "objetivo":
        props["status"] = "libre"
    return props


def _zone_props(nombre, kind, nota, author, color, opacity, folder):
    props = {"name": nombre, "folder": folder, "kind": kind, "description": nota,
             "author": author, "stroke": color, "stroke-width": 3,
             "fill": color, "fill-opacity": opacity, "labels": True}
    if kind == "objetivo":
        props["status"] = "libre"
    return props


def _origin_height(ctx, label):
    heights = ctx.heights or {}
    raw = heights.get(label, 1.7)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 1.7
    return float(raw)


def _linea_de_vista(args, actor, ctx):
    desde = _req_str(args, "desde", _REF_LIMIT)
    hasta = _req_str(args, "hasta", _REF_LIMIT)
    fa = _resolve(ctx, desde)
    if isinstance(fa, str):
        return fa
    fb = _resolve(ctx, hasta)
    if isinstance(fb, str):
        return fb
    if ctx.dem is None:
        return "No tengo datos de elevación cargados."
    vis, blocked, dist = line_of_sight(
        ctx.dem, fa.lat, fa.lon, _origin_height(ctx, fa.label),
        fb.lat, fb.lon, 1.7,
    )
    if vis is None:
        return "No tengo elevación en ese punto."
    span = format_distance(dist)
    if vis:
        return f"Desde {fa.label} a {fb.label} ({span}): visible."
    gap = format_distance(blocked)
    return (f"Desde {fa.label} a {fb.label} ({span}): no visible, "
            f"lo tapa el terreno a {gap} de {fa.label}.")


def _propose(ctx, actor, op, op_args, summary, geom=None, props=None):
    now = _now(ctx)
    fid = None
    if geom is not None:
        feat = ctx.layer.add_feature(props, geom, now, actor.uid)
        fid = feat["properties"]["id"]
    n = ctx.layer.add_proposal(op, op_args, actor.callsign, actor.uid, now, summary, fid=fid)
    if fid is not None:
        ctx.layer.update_feature(fid, {"proposal": n}, now, actor.uid)
    ctx.events.add("propuesta", f"{actor.callsign} propone: {summary} (#{n})", now)
    return f"Propuesta #{n}: {summary}. Un autorizado debe confirmarla."


def _marcar_punto(args, actor, ctx):
    nombre = _req_str(args, "nombre", 40)
    lugar = _req_str(args, "lugar", _REF_LIMIT)
    tipo = _req_enum(args, "tipo", _TIPOS)
    nota = _opt_str(args, "nota", 200)
    if not _take_write(ctx):
        return _LIMITE
    found = _resolve(ctx, lugar)
    if isinstance(found, str):
        return found
    kind = "objetivo" if tipo == "objetivo" else "punto"
    summary = f"marcar {nombre} ({tipo}) en {found.label}"
    geom = {"type": "Point", "coordinates": [found.lon, found.lat]}
    if not _can(actor):
        props = _point_props(nombre, kind, tipo, nota, actor.callsign, COLORS["propuesta"], FOLDER_PROPOSALS)
        replay = {"nombre": nombre, "lugar": lugar, "tipo": tipo, "nota": nota}
        return _propose(ctx, actor, "marcar_punto", replay, summary, geom, props)
    props = _point_props(nombre, kind, tipo, nota, actor.callsign, _point_color(tipo), FOLDER_GAME)
    feat = ctx.layer.add_feature(props, geom, _now(ctx), actor.uid)
    ctx.events.add("mapa", f"{actor.callsign}: {summary}", _now(ctx))
    return f"Marcado {nombre} ({tipo}) en {found.label}. id {feat['properties']['id']}."


def _dibujar_zona(args, actor, ctx):
    nombre = _req_str(args, "nombre", 40)
    lugar = _req_str(args, "lugar", _REF_LIMIT)
    radio = _req_int_range(args, "radio_m", 10, 300)
    kind = _req_enum(args, "kind", _ZONAS)
    nota = _opt_str(args, "nota", 200)
    if not _take_write(ctx):
        return _LIMITE
    found = _resolve(ctx, lugar)
    if isinstance(found, str):
        return found
    geom = {"type": "Polygon", "coordinates": [circle(found.lat, found.lon, radio)]}
    summary = f"dibujar {nombre} ({kind}, {radio} m) en {found.label}"
    if not _can(actor):
        props = _zone_props(nombre, kind, nota, actor.callsign, COLORS["propuesta"], 0.1, FOLDER_PROPOSALS)
        replay = {"nombre": nombre, "lugar": lugar, "radio_m": radio, "kind": kind, "nota": nota}
        return _propose(ctx, actor, "dibujar_zona", replay, summary, geom, props)
    color = COLORS["objetivo:libre"] if kind == "objetivo" else COLORS[kind]
    props = _zone_props(nombre, kind, nota, actor.callsign, color, 0.2, FOLDER_GAME)
    feat = ctx.layer.add_feature(props, geom, _now(ctx), actor.uid)
    ctx.events.add("mapa", f"{actor.callsign}: {summary}", _now(ctx))
    fid = feat["properties"]["id"]
    return f"Zona {nombre} ({kind}, {radio} m) en {found.label}. id {fid}."


def _estado_objetivo(args, actor, ctx):
    ref = _req_str(args, "objetivo", _REF_LIMIT)
    estado = _req_enum(args, "estado", _ESTADOS)
    if not _take_write(ctx):
        return _LIMITE
    feat, err = _match(ctx.layer, ref)
    if err is not None:
        return err
    name = feat["properties"].get("name", ref)
    if feat["properties"].get("kind") != "objetivo":
        return f"{name} no es un objetivo."
    summary = f"cambiar {name} a {estado}"
    if not _can(actor):
        return _propose(ctx, actor, "estado_objetivo", {"objetivo": ref, "estado": estado}, summary)
    color = COLORS[f"objetivo:{estado}"]
    changes = {"status": estado}
    if feat.get("geometry", {}).get("type") == "Polygon":
        changes["stroke"] = color
        changes["fill"] = color
    else:
        changes["marker-color"] = color
    ctx.layer.update_feature(feat["properties"]["id"], changes, _now(ctx), actor.uid)
    ctx.events.add("mapa", f"{actor.callsign}: {summary}", _now(ctx))
    return f"{name}: {estado}."


def _mover(args, actor, ctx):
    ref = _req_str(args, "objeto", _REF_LIMIT)
    lugar = _req_str(args, "lugar", _REF_LIMIT)
    if not _take_write(ctx):
        return _LIMITE
    found = _resolve(ctx, lugar)
    if isinstance(found, str):
        return found
    feat, err = _match(ctx.layer, ref)
    if err is not None:
        return err
    name = feat["properties"].get("name", ref)
    summary = f"mover {name} a {found.label}"
    if not _can(actor):
        return _propose(ctx, actor, "mover", {"objeto": ref, "lugar": lugar}, summary)
    ctx.layer.move_feature(feat["properties"]["id"], found.lat, found.lon, _now(ctx), actor.uid)
    ctx.events.add("mapa", f"{actor.callsign}: {summary}", _now(ctx))
    return f"Movido {name} a {found.label}."


def _borrar(args, actor, ctx):
    ref = _req_str(args, "objeto", _REF_LIMIT)
    if not _take_write(ctx):
        return _LIMITE
    feat, err = _match(ctx.layer, ref)
    if err is not None:
        return err
    props = feat["properties"]
    summary = f"borrar {props.get('name', ref)} ({props.get('id', '?')})"
    if not _can(actor):
        return _propose(ctx, actor, "borrar", {"objeto": ref}, summary)
    ctx.layer.delete_feature(props["id"], actor.uid)
    ctx.events.add("mapa", f"{actor.callsign}: {summary}", _now(ctx))
    return f"Borrado {props.get('name', ref)} ({props.get('id', '?')})."


def _recent_contact(ctx, actor, now):
    for feat in ctx.layer.features(FOLDER_GAME):
        props = feat.get("properties", {})
        if props.get("kind") != "contacto" or props.get("author_uid") != actor.uid:
            continue
        created = parse_iso_z(props.get("created"))
        if created is None:
            continue
        age = (now - created).total_seconds()
        if 0 <= age < 20:
            return True
    return False


def _reportar_contacto(args, actor, ctx):
    tipo = _req_enum(args, "tipo", _CONTACTS)
    cantidad = _opt_int(args, "cantidad", 1, 1, 50)
    lugar = _req_str(args, "lugar", _REF_LIMIT)
    nota = _opt_str(args, "nota", 200)
    found = _resolve(ctx, lugar)
    if isinstance(found, str):
        return found
    now = _now(ctx)
    if _recent_contact(ctx, actor, now):
        return "Espera unos segundos antes de otro reporte."
    nombre = f"{cantidad} {_CONTACT_LABEL[tipo]}"
    expires = now + timedelta(minutes=10)
    props = {"name": nombre, "folder": FOLDER_GAME, "kind": "contacto",
             "cot_type": _CONTACT_COT[tipo], "stale_minutes": 10,
             "expires": _iso_z(expires), "description": nota, "author": actor.callsign}
    geom = {"type": "Point", "coordinates": [found.lon, found.lat]}
    ctx.layer.add_feature(props, geom, now, actor.uid)
    offset = ctx.command_context.utc_offset_h
    moment = _local_hhmm(now, offset)
    ctx.layer.add_announcement(now, f"CONTACTO: {nombre} en {found.label} ({actor.callsign}, {moment}).", "todos")
    return f"Contacto publicado: {nombre} en {found.label}. Se borra a las {_local_hhmm(expires, offset)}."


def _path_length_m(path):
    total = 0.0
    for i in range(1, len(path)):
        total += haversine_m(path[i - 1][0], path[i - 1][1], path[i][0], path[i][1])
    return total


def _route_props(nombre, author, color, folder):
    return {"name": nombre, "folder": folder, "kind": "ruta", "author": author,
            "stroke": color, "stroke-width": 4, "labels": True}


def _ruta_cubierta(args, actor, ctx):
    desde = _req_str(args, "desde", _REF_LIMIT)
    hasta = _req_str(args, "hasta", _REF_LIMIT)
    if not _take_write(ctx):
        return _LIMITE
    fa = _resolve(ctx, desde)
    if isinstance(fa, str):
        return fa
    fb = _resolve(ctx, hasta)
    if isinstance(fb, str):
        return fb
    if ctx.exposure is None:
        return "No tengo el mapa de visibilidad cargado."
    if cell_of(ctx.exposure, fa.lat, fa.lon) is None or cell_of(ctx.exposure, fb.lat, fb.lon) is None:
        return "Ese punto queda fuera del mapa de visibilidad."
    path = covered_route(ctx.exposure, fa.lat, fa.lon, fb.lat, fb.lon)
    if not path:
        return "No encontré una ruta entre esos puntos."
    dist = format_distance(_path_length_m(path))
    pct = round(exposed_fraction(ctx.exposure, path) * 100)
    summary = f"trazar ruta cubierta {fa.label} → {fb.label}"
    name = f"Ruta cubierta {fa.label}→{fb.label}"
    geom = {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in path]}
    if not _can(actor):
        props = _route_props(name, actor.callsign, COLORS["propuesta"], FOLDER_PROPOSALS)
        replay = {"desde": desde, "hasta": hasta}
        return _propose(ctx, actor, "ruta_cubierta", replay, summary, geom, props)
    props = _route_props(name, actor.callsign, "#34c759", FOLDER_GAME)
    feat = ctx.layer.add_feature(props, geom, _now(ctx), actor.uid)
    ctx.events.add("mapa", f"{actor.callsign}: {summary}", _now(ctx))
    fid = feat["properties"]["id"]
    return f"Ruta cubierta {fa.label} → {fb.label} dibujada ({dist}, {pct} % expuesta). id {fid}."


def _deshacer(args, actor, ctx):
    if not _take_write(ctx):
        return _LIMITE
    return ctx.layer.undo_last(actor.uid)


def _programar_aviso(args, actor, ctx):
    minutos = _req_int_range(args, "minutos", 1, 240)
    texto = _req_str(args, "texto", 300)
    para = _req_enum(args, "para", _PARA)
    if not _can(actor):
        return "Solo un autorizado puede programar avisos."
    at = _now(ctx) + timedelta(minutes=minutos)
    n = ctx.layer.add_announcement(at, texto, para)
    return f"Aviso #{n} programado para las {_local_hhmm(at, ctx.command_context.utc_offset_h)}."


def _anunciar(args, actor, ctx):
    texto = _req_str(args, "texto", 300)
    if not _can(actor):
        return "Solo un autorizado puede anunciar a todos."
    ctx.layer.add_announcement(_now(ctx), texto, "todos")
    return "Anuncio enviado."


def _drop_proposal_feature(ctx, actor, fid):
    if not fid:
        return
    feat = ctx.layer.get(fid)
    if feat is None or feat.get("properties", {}).get("folder") != FOLDER_PROPOSALS:
        return
    try:
        ctx.layer.delete_feature(fid, actor.uid)
    except LayerError as err:
        if "no existe" not in str(err):
            raise


def _confirmar_propuesta(args, actor, ctx):
    numero = _req_int(args, "numero")
    aceptar = _req_bool(args, "aceptar")
    if not _can(actor):
        return "Solo un autorizado puede confirmar propuestas."
    prop = ctx.layer.pop_proposal(numero)
    if prop is None:
        return f"No existe la propuesta #{numero}."
    _drop_proposal_feature(ctx, actor, prop.get("fid"))
    if not aceptar:
        return f"Propuesta #{numero} descartada."
    again = Actor(prop["author_uid"], prop["author"], authorized=True)
    fresh = ToolContext(ctx.layer, ctx.events, ctx.command_context, writes_left=1,
                        dem=ctx.dem, exposure=ctx.exposure, heights=ctx.heights)
    result = execute(prop["op"], prop.get("args") or {}, again, fresh)
    return f"Propuesta #{numero} aceptada: {result}"


_DISPATCH = {
    "donde_esta": _donde_esta,
    "que_hay_en": _que_hay_en,
    "estado_equipo": _estado_equipo,
    "peligros": _peligros,
    "luz_y_clima": _luz_y_clima,
    "sitrep": _sitrep,
    "capa_juego": _capa_juego,
    "linea_de_vista": _linea_de_vista,
    "marcar_punto": _marcar_punto,
    "dibujar_zona": _dibujar_zona,
    "estado_objetivo": _estado_objetivo,
    "mover": _mover,
    "borrar": _borrar,
    "reportar_contacto": _reportar_contacto,
    "ruta_cubierta": _ruta_cubierta,
    "deshacer": _deshacer,
    "programar_aviso": _programar_aviso,
    "anunciar": _anunciar,
    "confirmar_propuesta": _confirmar_propuesta,
}
