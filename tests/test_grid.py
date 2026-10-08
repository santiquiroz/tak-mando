import math

import pytest

from mando.grid import Grid, cell_deg, grid_ref, parse_grid

GRID = Grid(5.1650, -75.4960, 100, 9, 9)


def test_cell_deg_values():
    dlat, dlon = cell_deg(GRID)
    assert dlat == pytest.approx(100 / 110574.0)
    ref_lat = 5.1650 - 9 * dlat / 2
    assert dlon == pytest.approx(100 / (111320.0 * math.cos(math.radians(ref_lat))))


def test_known_refs():
    assert grid_ref(GRID, 5.16110, -75.49175) == "E5"
    assert grid_ref(GRID, 5.16153, -75.49123) == "F4"
    assert grid_ref(GRID, 5.15874, -75.49269) == "D7"


def test_nw_corner_offset_is_a1():
    assert grid_ref(GRID, 5.1650 - 0.00001, -75.4960 + 0.00001) == "A1"


def test_outside_returns_none():
    # West of the grid.
    assert grid_ref(GRID, 5.16110, -75.49601) is None
    # South of row 9.
    dlat, _dlon = cell_deg(GRID)
    south = 5.1650 - 9 * dlat
    assert grid_ref(GRID, south - 0.00001, -75.49300) is None
    # North and east are also outside.
    assert grid_ref(GRID, 5.16501, -75.49500) is None
    assert grid_ref(GRID, 5.16110, -75.49000 + 0.05) is None


def test_parse_grid_round_trip():
    grid = parse_grid("5.1650,-75.4960,100,9,9")
    assert grid == GRID
    assert grid.north == pytest.approx(5.1650)
    assert grid.west == pytest.approx(-75.4960)
    assert grid.cell_m == pytest.approx(100.0)
    assert grid.cols == 9
    assert grid.rows == 9


def test_parse_grid_wrong_count():
    for text in ["", "5.1650,-75.4960,100,9", "5.1650,-75.4960,100,9,9,1"]:
        with pytest.raises(ValueError):
            parse_grid(text)


def test_parse_grid_not_numbers():
    for text in [
        "a,b,c,d,e",
        "5.1650,-75.4960,xxx,9,9",
        "5.1650,xxx,100,9,9",
        "xxx,-75.4960,100,9,9",
        "5.1650,-75.4960,100,9.5,9",
        "5.1650,-75.4960,100,9,x",
    ]:
        with pytest.raises(ValueError):
            parse_grid(text)


def test_parse_grid_bad_cell_m():
    for text in ["5.1650,-75.4960,0,9,9", "5.1650,-75.4960,-100,9,9"]:
        with pytest.raises(ValueError):
            parse_grid(text)


def test_parse_grid_bad_cols():
    for text in ["5.1650,-75.4960,100,0,9", "5.1650,-75.4960,100,27,9"]:
        with pytest.raises(ValueError):
            parse_grid(text)


def test_parse_grid_bad_rows():
    for text in ["5.1650,-75.4960,100,9,0", "5.1650,-75.4960,100,9,-3"]:
        with pytest.raises(ValueError):
            parse_grid(text)


def test_parse_grid_errors_are_spanish():
    with pytest.raises(ValueError, match="cuadrícula"):
        parse_grid("1,2,3")
