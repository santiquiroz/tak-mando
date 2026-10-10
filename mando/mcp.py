"""Stdio MCP server exposing the map tools."""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mando import __version__
from mando.__main__ import bbox_center
from mando.commands import Context
from mando.elevation import load_dted
from mando.events import EventLog
from mando.exposure import load_exposure
from mando.grid import select_grid
from mando.layer import Layer, parse_iso_z
from mando.roster import Player
from mando.sun import moon_illumination, sun_events
from mando.tools import TOOLS, Actor, ToolContext, execute, load_heights
from mando.zones import load_places, load_zones

_STALE_TOOLS = frozenset({"donde_esta", "estado_equipo", "sitrep"})
_NO_STATUS = "(Sin datos de jugadores: el bot no está corriendo.) "
_MCP_ACTOR = Actor("mcp", "PC", authorized=True, is_mcp=True)


def _num(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _status_players(items, now):
    players = []
    for item in items:
        if not isinstance(item, dict):
            continue
        seen = parse_iso_z(item.get("last_seen")) or now
        players.append(Player(
            uid=str(item.get("uid", "")),
            callsign=str(item.get("callsign", "")),
            lat=_num(item.get("lat")),
            lon=_num(item.get("lon")),
            last_seen=seen,
            stale=None,
        ))
    return players


def load_status(path, now):
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ([], EventLog(), None)
    if not isinstance(data, dict):
        return ([], EventLog(), None)
    players = _status_players(data.get("players") or [], now)
    events = EventLog.from_list(data.get("events") or [])
    updated = parse_iso_z(data.get("updated"))
    if updated is None:
        return (players, events, None)
    return (players, events, max(0.0, (now - updated).total_seconds()))


def _stale_prefix(age):
    if age is None:
        return _NO_STATUS
    if age > 60:
        return f"(Datos de jugadores de hace {int(age)} s.) "
    return ""


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _tool_list():
    tools = []
    for item in TOOLS:
        fn = item["function"]
        tools.append({"name": fn["name"], "description": fn["description"],
                      "inputSchema": fn["parameters"]})
    return {"tools": tools}


class McpServer:
    def __init__(self, layer, zones, places, grid, status_path, lat, lon,
                 utc_offset_h=-5.0, clock=lambda: datetime.now(timezone.utc),
                 dem=None, exposure=None, heights=None):
        self._layer = layer
        self._zones = zones
        self._places = places
        self._grid = grid
        self._status_path = status_path
        self._lat = lat
        self._lon = lon
        self._utc_offset_h = utc_offset_h
        self._clock = clock
        self._dem = dem
        self._exposure = exposure
        self._heights = heights

    def handle(self, message):
        if not isinstance(message, dict):
            return _err(None, -32700, "Parse error")
        method = message.get("method", "")
        if isinstance(method, str) and method.startswith("notifications/"):
            return None
        if "id" not in message:
            return None
        rid = message["id"]
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        if method == "initialize":
            return _ok(rid, self._initialize(params))
        if method == "ping":
            return _ok(rid, {})
        if method == "tools/list":
            return _ok(rid, _tool_list())
        if method == "tools/call":
            return _ok(rid, self._call(params))
        return _err(rid, -32601, "Method not found")

    def _initialize(self, params):
        return {"protocolVersion": params.get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "tak-mando", "version": __version__}}

    def _context(self, now, players):
        day = (now + timedelta(hours=self._utc_offset_h)).date()
        try:
            sun = sun_events(day, self._lat, self._lon, self._utc_offset_h)
        except Exception:
            sun = {}
        return Context(now_utc=now, utc_offset_h=self._utc_offset_h, requester=None,
                       players=players, places=self._places, zones=self._zones,
                       sun=sun, moon=moon_illumination(now), hours=[],
                       forecast_age_s=None, grid=self._grid)

    def _call(self, params):
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        now = self._clock()
        players, events, age = load_status(self._status_path, now)
        ctx = ToolContext(self._layer, events, self._context(now, players), writes_left=20,
                          dem=self._dem, exposure=self._exposure, heights=self._heights)
        out = execute(name, args, _MCP_ACTOR, ctx)
        if name in _STALE_TOOLS:
            out = (_stale_prefix(age) + out)[:600]
        return {"content": [{"type": "text", "text": out}], "isError": False}


def _build_parser():
    parser = argparse.ArgumentParser(description="Servidor MCP de Mando por stdio.")
    parser.add_argument("--layer", default="juego.geojson")
    parser.add_argument("--state", default="mando-state.json")
    parser.add_argument("--status", default="mando-status.json")
    parser.add_argument("--zones", required=True, help="GeoJSON del terreno")
    parser.add_argument("--dted", default=None, help="DTED de elevación para línea de vista")
    parser.add_argument("--exposure", default=None, help="JSON de visibilidad para rutas cubiertas")
    parser.add_argument("--buildings", default=None, help="GeoJSON de edificios (lugares y alturas)")
    parser.add_argument("--grid", default=None,
                        help="cuadrícula GRG NORTH,WEST,CELL_M,COLS,ROWS")
    parser.add_argument("--grid-file", default=None,
                        help="JSON de cuadrícula rotada o con etiquetas propias (gana sobre --grid)")
    parser.add_argument("--tz-offset", type=float, default=-5.0)
    return parser


def _load_field(path):
    zones = load_zones(path)
    places = load_places(path)
    center = bbox_center(path)
    if center is None:
        raise ValueError(f"{path} no contiene coordenadas")
    return (zones, places, center)


def _load_optional(path, loader, label):
    if not path:
        return None
    try:
        return loader(path)
    except (OSError, ValueError) as exc:
        print(f"mando-mcp: {label} ({exc})", file=sys.stderr)
        return None


def _serve(server):
    for line in sys.stdin:
        if line.strip() == "":
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            resp = _err(None, -32700, "Parse error")
            print(json.dumps(resp, ensure_ascii=False), flush=True)
            continue
        try:
            resp = server.handle(message)
        except Exception:
            rid = message.get("id") if isinstance(message, dict) else None
            resp = _err(rid, -32603, "Internal error")
        if resp is not None:
            print(json.dumps(resp, ensure_ascii=False), flush=True)


def main(argv=None):
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        grid = select_grid(args.grid, args.grid_file)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        zones, places, center = _load_field(args.zones)
    except (OSError, ValueError) as exc:
        print(f"mando-mcp: no se pudo leer {args.zones}: {exc}", file=sys.stderr)
        return 1
    dem = _load_optional(args.dted, load_dted, "sin DTED")
    exposure = _load_optional(args.exposure, load_exposure, "sin mapa de visibilidad")
    heights = None
    if args.buildings:
        try:
            extra = load_places(args.buildings)
            heights = load_heights(args.buildings)
        except (OSError, ValueError) as exc:
            print(f"mando-mcp: sin edificios ({exc})", file=sys.stderr)
        else:
            places = places + extra
    layer = Layer(args.layer, args.state)
    server = McpServer(layer, zones, places, grid, args.status, center[0], center[1],
                       utc_offset_h=args.tz_offset, dem=dem, exposure=exposure, heights=heights)
    _serve(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
