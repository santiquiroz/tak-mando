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


def test_unknown_building_number_is_not_a_name_match():
    assert _resolve("1").startswith("No encuentro '1'")


def test_duplicate_building_name_resolves_to_first():
    dup = [
        Place("12 Torre sur", 5.1594, -75.4934, None),
        Place("12 Torre sur", 5.1600, -75.4940, None),
    ]
    r = resolve_place("torre sur", grid=GRID, places=dup, players=[RECON, ME], requester=ME)
    assert isinstance(r, Resolved)
    assert r.label == "12 Torre sur"
    assert (r.lat, r.lon) == (5.1594, -75.4934)


def test_different_names_stay_ambiguous():
    places = [
        Place("12 Torre sur", 5.1594, -75.4934, None),
        Place("11 Torre oeste", 5.1600, -75.4940, None),
    ]
    assert (
        resolve_place("torre", grid=GRID, places=places, players=[RECON, ME], requester=ME)
        == "Hay varios lugares: 12 Torre sur, 11 Torre oeste. ¿Cuál?"
    )


OFFICIAL_GRID = Grid(north=5.1614426, west=-75.4895678, cell_m=25.66, cols=15, rows=17, row_m=20.44,
                     col_bearing_deg=207.73, row_bearing_deg=297.41, labels="ABCDEFGHIJGKLMN")


def _resolve_official(text):
    return resolve_place(text, grid=OFFICIAL_GRID, places=PLACES, players=[], requester=None)


def test_official_grid_cell_resolves_to_its_centre():
    r = _resolve_official("H9")
    assert isinstance(r, Resolved) and r.label == "H9"
    assert (r.lat, r.lon) == cell_center(OFFICIAL_GRID, "H9")
    assert grid_ref(OFFICIAL_GRID, r.lat, r.lon) == "H9"


def test_official_grid_unknown_cell_and_far_point():
    assert _resolve_official("Z3") == "El cuadro Z3 no existe en este mapa."
    assert _resolve_official("H18") == "El cuadro H18 no existe en este mapa."
    assert _resolve_official("5.1714426,-75.4895678") == "Ese punto queda fuera del campo."
