import json
import math

import pytest

from mando.geo import haversine_m
from mando.grid import (
    Grid,
    a1_corner_direction,
    cell_center,
    cell_deg,
    distance_outside_m,
    row_size_m,
    grid_ref,
    load_grid_file,
    parse_grid,
    select_grid,
)

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


OFFICIAL = {"north": 5.1614426, "west": -75.4895678, "cell_m": 25.66, "row_m": 20.44,
            "cols": 15, "rows": 17, "col_bearing_deg": 207.73, "row_bearing_deg": 297.41,
            "labels": "ABCDEFGHIJGKLMN"}
OFFICIAL_GRID = Grid(**OFFICIAL)


def _write_json(tmp_path, data, name="grid.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_official_known_refs():
    assert grid_ref(OFFICIAL_GRID, 5.1599791, -75.49091) == "I3"
    assert grid_ref(OFFICIAL_GRID, 5.1604224, -75.4914393) == "H7"
    assert grid_ref(OFFICIAL_GRID, 5.1615444, -75.4914919) == "D10"
    assert grid_ref(OFFICIAL_GRID, 5.1598859, -75.4923613) == "K10"


def test_official_cell_center_round_trip():
    lat, lon = cell_center(OFFICIAL_GRID, "H9")
    assert grid_ref(OFFICIAL_GRID, lat, lon) == "H9"


def test_official_duplicated_g_resolves_to_seventh_column():
    g = cell_center(OFFICIAL_GRID, "g14")
    assert grid_ref(OFFICIAL_GRID, *g) == "G14"
    assert haversine_m(*g, *cell_center(OFFICIAL_GRID, "F14")) == pytest.approx(25.66, abs=0.2)
    assert haversine_m(*g, *cell_center(OFFICIAL_GRID, "H14")) == pytest.approx(25.66, abs=0.2)


def test_official_k_is_twelfth_column():
    k = cell_center(OFFICIAL_GRID, "K10")
    assert grid_ref(OFFICIAL_GRID, *k) == "K10"
    assert haversine_m(*k, *cell_center(OFFICIAL_GRID, "J10")) == pytest.approx(2 * 25.66, abs=0.3)
    assert haversine_m(*k, *cell_center(OFFICIAL_GRID, "L10")) == pytest.approx(25.66, abs=0.2)


def test_official_invalid_labels():
    for ref in ["Z3", "H0", "H18", "", "H", "9H"]:
        assert cell_center(OFFICIAL_GRID, ref) is None


def test_official_distance_outside():
    lat, lon = cell_center(OFFICIAL_GRID, "H9")
    assert distance_outside_m(OFFICIAL_GRID, lat, lon) == 0.0
    assert distance_outside_m(OFFICIAL_GRID, 5.1614426 + 0.01, -75.4895678) > 500


def test_official_outside_returns_none():
    assert grid_ref(OFFICIAL_GRID, 5.1614426 + 0.001, -75.4895678) is None


def test_default_grid_cell_center_and_distance_unchanged():
    lat, lon = cell_center(GRID, "E5")
    dlat, dlon = cell_deg(GRID)
    assert (lat, lon) == pytest.approx((5.1650 - 4.5 * dlat, -75.4960 + 4.5 * dlon))
    assert distance_outside_m(GRID, lat, lon) == 0.0
    assert distance_outside_m(GRID, 4.0, -74.0) > 500


def test_load_grid_file_happy_path(tmp_path):
    grid = load_grid_file(_write_json(tmp_path, dict(OFFICIAL, name="Cementos Caldas")))
    assert grid == OFFICIAL_GRID
    assert grid_ref(grid, 5.1604224, -75.4914393) == "H7"


def test_load_grid_file_rejects_bad_labels(tmp_path):
    path = _write_json(tmp_path, dict(OFFICIAL, labels="ABCDEFGHIJGKLM"))
    with pytest.raises(ValueError, match="cuadrícula"):
        load_grid_file(path)


def test_load_grid_file_rejects_bad_sizes(tmp_path):
    path = _write_json(tmp_path, dict(OFFICIAL, row_m=0))
    with pytest.raises(ValueError, match="cuadrícula"):
        load_grid_file(path)


def test_load_grid_file_rejects_missing_key_and_bad_json(tmp_path):
    missing = dict(OFFICIAL)
    del missing["col_bearing_deg"]
    with pytest.raises(ValueError, match="col_bearing_deg"):
        load_grid_file(_write_json(tmp_path, missing))
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="cuadrícula"):
        load_grid_file(str(broken))
    with pytest.raises(ValueError, match="cuadrícula"):
        load_grid_file(str(tmp_path / "nope.json"))


def test_select_grid_file_wins_over_text(tmp_path):
    path = _write_json(tmp_path, OFFICIAL)
    assert select_grid("5.1650,-75.4960,100,9,9", path) == OFFICIAL_GRID
    assert select_grid("5.1650,-75.4960,100,9,9", None) == GRID
    assert select_grid(None, None) is None


def test_a1_corner_direction():
    assert a1_corner_direction(GRID) == "noroeste"
    assert a1_corner_direction(OFFICIAL_GRID) == "este"


def test_repeated_column_letter_is_marked_as_second():
    grid = Grid(north=5.1614426, west=-75.4895678, cell_m=25.66, cols=15, rows=17, row_m=20.44,
                col_bearing_deg=207.73, row_bearing_deg=297.41, labels="ABCDEFGHIJGKLMN")
    first_g = cell_center(grid, "G10")
    assert grid_ref(grid, *first_g) == "G10"
    from mando.grid import _latlon_of_uv
    second_g = _latlon_of_uv(grid, 10.5 * grid.cell_m, 9.5 * row_size_m(grid))
    assert grid_ref(grid, *second_g) == "G10 (2ª G)"
