"""Command line: python -m mando PACKAGE.zip --zones FIELD.geojson ..."""

from __future__ import annotations

import argparse
import json
import sys

from mando.bot import run
from mando.grid import select_grid


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bot acompañante para un servidor TAK: geocercas, "
        "comandos de chat, avisos de luz y lluvia, y pérdida de contacto.",
    )
    parser.add_argument("package", help="paquete de conexión .zip del bot")
    parser.add_argument("--zones", required=True,
                        help="GeoJSON del terreno (polígonos y lugares)")
    parser.add_argument("--dted", default=None,
                        help="DTED de elevación para línea de vista")
    parser.add_argument("--exposure", default=None,
                        help="JSON de visibilidad para rutas cubiertas")
    parser.add_argument("--buildings", default=None,
                        help="GeoJSON de edificios (lugares y alturas)")
    parser.add_argument("--host", default=None,
                        help="servidor TAK (por defecto, el del paquete)")
    parser.add_argument("--port", type=int, default=None,
                        help="puerto TLS (por defecto, el del paquete)")
    parser.add_argument("--callsign", default="Mando")
    parser.add_argument("--uid", default="mando-bot")
    parser.add_argument("--tz-offset", type=float, default=-5)
    parser.add_argument("--lat", type=float, default=None,
                        help="latitud del bot (por defecto, centro del GeoJSON)")
    parser.add_argument("--lon", type=float, default=None,
                        help="longitud del bot (por defecto, centro del GeoJSON)")
    parser.add_argument("--lost-after", type=int, default=300,
                        help="segundos sin reportar antes de avisar (0 desactiva)")
    parser.add_argument("--geofence-cooldown", type=int, default=180,
                        help="segundos entre avisos de la misma zona y jugador")
    parser.add_argument("--rain-mm", type=float, default=2.0,
                        help="mm/h que cuentan como lluvia fuerte")
    parser.add_argument("--no-announce", action="store_true",
                        help="no enviar avisos de luz ni lluvia")
    parser.add_argument("--no-weather", action="store_true",
                        help="no consultar el pronóstico (ni en segundo plano)")
    parser.add_argument("--dry-run", action="store_true",
                        help="conectar y escuchar, pero mostrar los chats "
                        "salientes en vez de enviarlos")
    parser.add_argument("--openssl", default="openssl",
                        help="ejecutable openssl (con proveedor legacy)")
    parser.add_argument("--ignore-prefix", action="append", default=None,
                        help="prefijo de uid a ignorar (repetible, "
                        "por defecto: overlay-)")
    parser.add_argument("--share-package", default=None,
                        help="zip del campo a entregar a los jugadores")
    parser.add_argument("--share-name", default="Paquete del campo",
                        help="nombre del paquete para los jugadores")
    parser.add_argument("--share-url-base", default=None,
                        help="base del enlace Marti (por defecto, "
                        "https://<servidor>:8443)")
    parser.add_argument("--share-state", default=None,
                        help="JSON donde recordar a quién se envió "
                        "el paquete")
    parser.add_argument("--grid", default=None,
                        help="cuadrícula GRG NORTH,WEST,CELL_M,COLS,ROWS "
                        "(p. ej. 5.1650,-75.4960,100,9,9)")
    parser.add_argument("--grid-file", default=None,
                        help="JSON de cuadrícula rotada o con etiquetas propias "
                        "(gana sobre --grid)")
    parser.add_argument("--layer", default=None,
                        help="GeoJSON de la capa de juego (juego.geojson)")
    parser.add_argument("--state", default=None,
                        help="JSON de estado del juego "
                        "(por defecto, <layer>.state.json)")
    parser.add_argument("--status", default=None,
                        help="JSON de estado para lectores "
                        "(mando-status.json)")
    parser.add_argument("--admin-uid", action="append", default=None,
                        help="uid autorizado inicial (repetible)")
    parser.add_argument("--llm-url", default=None,
                        help="base OpenAI-compatible del cerebro o 'auto' "
                        "(puerta de enlace de WSL)")
    parser.add_argument("--llm-model", default="claude-sonnet-4-6",
                        help="modelo del cerebro")
    parser.add_argument("--event-name", default="la partida",
                        help="nombre de la partida para el cerebro")
    return parser


def _iter_positions(node):
    if isinstance(node, list) and node:
        if isinstance(node[0], (int, float)):
            if len(node) >= 2 and isinstance(node[1], (int, float)):
                yield (node[0], node[1])
        else:
            for child in node:
                yield from _iter_positions(child)


def bbox_center(path):
    """Centre (lat, lon) del bounding box de todas las coordenadas."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    lons: list[float] = []
    lats: list[float] = []
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "coordinates" in node:
                for lon, lat in _iter_positions(node["coordinates"]):
                    lons.append(float(lon))
                    lats.append(float(lat))
            else:
                stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    if not lons:
        return None
    return ((min(lats) + max(lats)) / 2.0, (min(lons) + max(lons)) / 2.0)


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.grid = select_grid(args.grid, args.grid_file)
    except ValueError as exc:
        parser.error(str(exc))
    if args.ignore_prefix is None:
        args.ignore_prefix = ["overlay-"]
    if args.lat is None or args.lon is None:
        try:
            center = bbox_center(args.zones)
        except (OSError, ValueError) as exc:
            print(f"mando: no se pudo leer {args.zones}: {exc}",
                  file=sys.stderr)
            return 1
        if center is None:
            print(f"mando: {args.zones} no contiene coordenadas; "
                  "indica --lat/--lon", file=sys.stderr)
            return 1
        if args.lat is None:
            args.lat = center[0]
        if args.lon is None:
            args.lon = center[1]
    try:
        return run(args)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as exc:
        print(f"mando: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
