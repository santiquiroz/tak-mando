"""Resolve place references to coordinates."""

from __future__ import annotations

import re
from dataclasses import dataclass

from mando.geo import haversine_m
from mando.grid import Grid, cell_deg, grid_ref
from mando.layer import normalize
from mando.roster import Player
from mando.zones import Place

_CELL_RE = re.compile(r"^[a-z]\d{1,2}$")
_COORD_RE = re.compile(r"^-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?$")
_NUMBER_RE = re.compile(r"^\d{1,3}$")
_LEADING_NUM_RE = re.compile(r"^\d+\s+")
_HERE_WORDS = {"aqui", "mi posicion", "donde estoy", "yo"}


@dataclass
class Resolved:
    lat: float
    lon: float
    label: str


def cell_center(grid: Grid | None, ref: str) -> tuple[float, float] | None:
    if grid is None:
        return None
    norm = normalize(ref)
    if not _CELL_RE.match(norm):
        return None
    col = ord(norm[0]) - ord("a")
    row = int(norm[1:]) - 1
    if col < 0 or col >= grid.cols or row < 0 or row >= grid.rows:
        return None
    dlat, dlon = cell_deg(grid)
    return (grid.north - (row + 0.5) * dlat, grid.west + (col + 0.5) * dlon)


def _empty(norm: str) -> str | None:
    if norm == "":
        return "Dime un lugar: un cuadro (E5), un edificio (12 o torre sur) o un callsign."
    return None


def _here(norm: str, grid: Grid | None, requester: Player | None) -> Resolved | str | None:
    if norm not in _HERE_WORDS:
        return None
    if requester is None or requester.lat is None or requester.lon is None:
        return "No tengo tu posición todavía."
    ref = grid_ref(grid, requester.lat, requester.lon) if grid is not None else None
    label = f"tu posición ({ref})" if ref else "tu posición"
    return Resolved(requester.lat, requester.lon, label)


def _cell(norm: str, grid: Grid | None) -> Resolved | str | None:
    if not _CELL_RE.match(norm):
        return None
    label = norm.upper()
    if grid is None:
        return f"El cuadro {label} no existe en este mapa."
    center = cell_center(grid, norm)
    if center is None:
        return f"El cuadro {label} no existe en este mapa."
    lat, lon = center
    return Resolved(lat, lon, label)


def _coords(norm: str) -> Resolved | None:
    if not _COORD_RE.match(norm):
        return None
    lat_raw, lon_raw = norm.split(",", 1)
    lat = float(lat_raw.strip())
    lon = float(lon_raw.strip())
    return Resolved(lat, lon, f"{lat:.5f},{lon:.5f}")


def _building_number(norm: str, places: list[Place]) -> Resolved | None:
    if not _NUMBER_RE.match(norm):
        return None
    for place in places or []:
        if normalize(place.name).startswith(norm + " "):
            return Resolved(place.lat, place.lon, place.name)
    return None


def _place_name(norm: str, places: list[Place]) -> Resolved | str | None:
    if norm == "":
        return None
    matches = []
    for place in places or []:
        full = normalize(place.name)
        stripped = _LEADING_NUM_RE.sub("", full).strip()
        if norm == full or norm in stripped:
            matches.append(place)
    if len(matches) > 1:
        names = ", ".join(p.name for p in matches)
        return f"Hay varios lugares: {names}. ¿Cuál?"
    if len(matches) == 1:
        found = matches[0]
        return Resolved(found.lat, found.lon, found.name)
    return None


def _callsign(norm: str, grid: Grid | None, players: list[Player]) -> Resolved | None:
    if norm == "":
        return None
    known = [p for p in players or [] if p.lat is not None and p.lon is not None]
    for player in known:
        if normalize(player.callsign) == norm:
            ref = grid_ref(grid, player.lat, player.lon) if grid is not None else None
            label = f"{player.callsign} ({ref})" if ref else player.callsign
            return Resolved(player.lat, player.lon, label)
    prefixed = [p for p in known if normalize(p.callsign).startswith(norm)]
    if len(prefixed) == 1:
        player = prefixed[0]
        ref = grid_ref(grid, player.lat, player.lon) if grid is not None else None
        label = f"{player.callsign} ({ref})" if ref else player.callsign
        return Resolved(player.lat, player.lon, label)
    return None


def _unknown(text: str) -> str:
    return f"No encuentro '{(text or '').strip()}'. Usa un cuadro (E5), un edificio (12 o torre sur) o un callsign."


def _outside_field(grid: Grid | None, lat: float, lon: float) -> bool:
    if grid is None:
        return False
    dlat, dlon = cell_deg(grid)
    south = grid.north - grid.rows * dlat
    east = grid.west + grid.cols * dlon
    near_lat = min(max(lat, south), grid.north)
    near_lon = min(max(lon, grid.west), east)
    return haversine_m(lat, lon, near_lat, near_lon) > 500


def resolve_place(
    text: str,
    *,
    grid: Grid | None,
    places: list[Place],
    players: list[Player],
    requester: Player | None,
) -> Resolved | str:
    norm = normalize(text)
    rules = (
        lambda: _empty(norm),
        lambda: _here(norm, grid, requester),
        lambda: _cell(norm, grid),
        lambda: _coords(norm),
        lambda: _building_number(norm, places),
        lambda: _place_name(norm, places),
        lambda: _callsign(norm, grid, players),
        lambda: _unknown(text),
    )
    for rule in rules:
        found = rule()
        if found is None:
            continue
        if isinstance(found, str):
            return found
        if _outside_field(grid, found.lat, found.lon):
            return "Ese punto queda fuera del campo."
        return found
    return _unknown(text)
