from datetime import datetime, timezone

from mando.grid import Grid, grid_ref
from mando.places import Resolved, cell_center, resolve_place
from mando.roster import Player
from mando.zones import Place

NOW = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)
GRID = Grid(north=5.1650, west=-75.4960, cell_m=100, cols=9, rows=9)
PLACES = [
    Place("12 Torre sur", 5.1594, -75.4934, None),
    Place("2 Silo 1", 5.1617, -75.4908, None),
    Place("3 Silo 2", 5.1612, -75.4908, None),
]
RECON = Player("u-recon", "Recon", 5.1610, -75.4920, NOW, None)
ME = Player("u-me", "santi", 5.1605, -75.4930, NOW, None)


def _resolve(text, requester=ME):
    return resolve_place(text, grid=GRID, places=PLACES, players=[RECON, ME], requester=requester)


def test_cell_center_inside_its_cell():
    lat, lon = cell_center(GRID, "E5")
    assert grid_ref(GRID, lat, lon) == "E5"
    assert cell_center(GRID, "Z1") is None


def test_grid_reference():
    r = _resolve("e5")
    assert isinstance(r, Resolved) and r.label == "E5"
    assert grid_ref(GRID, r.lat, r.lon) == "E5"


def test_grid_reference_outside():
    assert _resolve("E12") == "El cuadro E12 no existe en este mapa."


def test_building_by_number_and_name():
    assert _resolve("12").label == "12 Torre sur"
    assert _resolve("torre sur").label == "12 Torre sur"
    assert _resolve("Torre Súr").label == "12 Torre sur"


def test_ambiguous_building_name():
    assert _resolve("silo") == "Hay varios lugares: 2 Silo 1, 3 Silo 2. ¿Cuál?"


def test_player_callsign():
    r = _resolve("rec")
    assert r.label.startswith("Recon (") and (r.lat, r.lon) == (RECON.lat, RECON.lon)


def test_here_uses_requester():
    r = _resolve("aquí")
    assert (r.lat, r.lon) == (ME.lat, ME.lon)
    assert r.label.startswith("tu posición")
    assert _resolve("aqui", requester=None) == "No tengo tu posición todavía."


def test_coordinates():
    r = _resolve("5.16, -75.49")
    assert (r.lat, r.lon) == (5.16, -75.49)


def test_outside_field():
    assert _resolve("4.0,-74.0") == "Ese punto queda fuera del campo."


def test_unknown_and_empty():
    assert _resolve("xyz").startswith("No encuentro 'xyz'")
    assert _resolve("  ").startswith("Dime un lugar")
