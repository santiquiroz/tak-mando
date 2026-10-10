from __future__ import annotations

import json
import math
import re
import string
from dataclasses import dataclass

from mando.geo import haversine_m

_M_PER_DEG_LAT = 110574.0
_M_PER_DEG_LON = 111320.0
_DEFAULT_COL_BEARING = 90.0
_DEFAULT_ROW_BEARING = 180.0
_REF_RE = re.compile(r"^([A-Z])(\d{1,2})$", re.ASCII)
_COMPASS = ("norte", "noreste", "este", "sureste", "sur", "suroeste", "oeste", "noroeste")
_FILE_KEYS = ("north", "west", "cell_m", "row_m", "cols", "rows",
              "col_bearing_deg", "row_bearing_deg", "labels")
_MIN_AXIS_SINE = 0.5


@dataclass
class Grid:
    north: float
    west: float
    cell_m: float
    cols: int
    rows: int
    row_m: float | None = None
    col_bearing_deg: float = _DEFAULT_COL_BEARING
    row_bearing_deg: float = _DEFAULT_ROW_BEARING
    labels: str | None = None


def cell_deg(grid: Grid) -> tuple[float, float]:
    dlat = grid.cell_m / _M_PER_DEG_LAT
    ref_lat = grid.north - grid.rows * dlat / 2
    dlon = grid.cell_m / (_M_PER_DEG_LON * math.cos(math.radians(ref_lat)))
    return (dlat, dlon)


def row_size_m(grid: Grid) -> float:
    return grid.cell_m if grid.row_m is None else grid.row_m


def column_labels(grid: Grid) -> str:
    if grid.labels is None:
        return string.ascii_uppercase[:grid.cols]
    return grid.labels


def _north_aligned(grid: Grid) -> bool:
    bearings = (grid.col_bearing_deg, grid.row_bearing_deg)
    if bearings != (_DEFAULT_COL_BEARING, _DEFAULT_ROW_BEARING):
        return False
    return row_size_m(grid) == grid.cell_m


def is_default_layout(grid: Grid) -> bool:
    if not _north_aligned(grid):
        return False
    return column_labels(grid) == string.ascii_uppercase[:grid.cols]


def _axis(bearing_deg: float) -> tuple[float, float]:
    rad = math.radians(bearing_deg)
    return (math.sin(rad), math.cos(rad))


def _to_local_m(grid: Grid, lat: float, lon: float) -> tuple[float, float]:
    east = (lon - grid.west) * _M_PER_DEG_LON * math.cos(math.radians(grid.north))
    north = (lat - grid.north) * _M_PER_DEG_LAT
    return (east, north)


def _from_local_m(grid: Grid, east: float, north: float) -> tuple[float, float]:
    lat = grid.north + north / _M_PER_DEG_LAT
    lon = grid.west + east / (_M_PER_DEG_LON * math.cos(math.radians(grid.north)))
    return (lat, lon)


def _local_to_uv(grid: Grid, east: float, north: float) -> tuple[float, float]:
    cx, cy = _axis(grid.col_bearing_deg)
    rx, ry = _axis(grid.row_bearing_deg)
    return (east * cx + north * cy, east * rx + north * ry)


def _uv_to_local(grid: Grid, u: float, v: float) -> tuple[float, float]:
    cx, cy = _axis(grid.col_bearing_deg)
    rx, ry = _axis(grid.row_bearing_deg)
    det = cx * ry - cy * rx
    return ((u * ry - cy * v) / det, (cx * v - rx * u) / det)


def _uv_of(grid: Grid, lat: float, lon: float) -> tuple[float, float]:
    east, north = _to_local_m(grid, lat, lon)
    return _local_to_uv(grid, east, north)


def _latlon_of_uv(grid: Grid, u: float, v: float) -> tuple[float, float]:
    east, north = _uv_to_local(grid, u, v)
    return _from_local_m(grid, east, north)


def _label(grid: Grid, col: int, row: int) -> str | None:
    if col < 0 or col >= grid.cols or row < 0 or row >= grid.rows:
        return None
    labels = column_labels(grid)
    letter = labels[col]
    # A printed map can repeat a column letter; a bare "G10" would send players to the first G column.
    if labels.index(letter) != col:
        return f"{letter}{row + 1} (2ª {letter})"
    return f"{letter}{row + 1}"


def _north_aligned_ref(grid: Grid, lat: float, lon: float) -> str | None:
    dlat, dlon = cell_deg(grid)
    col = math.floor((lon - grid.west) / dlon)
    row = math.floor((grid.north - lat) / dlat)
    return _label(grid, col, row)


def _rotated_ref(grid: Grid, lat: float, lon: float) -> str | None:
    u, v = _uv_of(grid, lat, lon)
    col = math.floor(u / grid.cell_m)
    row = math.floor(v / row_size_m(grid))
    return _label(grid, col, row)


def grid_ref(grid: Grid, lat: float, lon: float) -> str | None:
    if lat is None or lon is None:
        return None
    if _north_aligned(grid):
        return _north_aligned_ref(grid, lat, lon)
    return _rotated_ref(grid, lat, lon)


def _parse_ref(grid: Grid, ref: str) -> tuple[int, int] | None:
    match = _REF_RE.match(str(ref or "").strip().upper())
    if match is None:
        return None
    col = column_labels(grid).upper().find(match.group(1))
    row = int(match.group(2)) - 1
    if col < 0 or row < 0 or row >= grid.rows:
        return None
    return (col, row)


def cell_center(grid: Grid | None, ref: str) -> tuple[float, float] | None:
    if grid is None:
        return None
    cell = _parse_ref(grid, ref)
    if cell is None:
        return None
    col, row = cell
    if _north_aligned(grid):
        dlat, dlon = cell_deg(grid)
        return (grid.north - (row + 0.5) * dlat, grid.west + (col + 0.5) * dlon)
    return _latlon_of_uv(grid, (col + 0.5) * grid.cell_m, (row + 0.5) * row_size_m(grid))


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _north_aligned_distance_m(grid: Grid, lat: float, lon: float) -> float:
    dlat, dlon = cell_deg(grid)
    south = grid.north - grid.rows * dlat
    east = grid.west + grid.cols * dlon
    near_lat = _clamp(lat, south, grid.north)
    near_lon = _clamp(lon, grid.west, east)
    return haversine_m(lat, lon, near_lat, near_lon)


def _rotated_distance_m(grid: Grid, lat: float, lon: float) -> float:
    u, v = _uv_of(grid, lat, lon)
    near_u = _clamp(u, 0.0, grid.cols * grid.cell_m)
    near_v = _clamp(v, 0.0, grid.rows * row_size_m(grid))
    if (near_u, near_v) == (u, v):
        return 0.0
    east, north = _to_local_m(grid, lat, lon)
    near_east, near_north = _uv_to_local(grid, near_u, near_v)
    return math.hypot(east - near_east, north - near_north)


def distance_outside_m(grid: Grid, lat: float, lon: float) -> float:
    if _north_aligned(grid):
        return _north_aligned_distance_m(grid, lat, lon)
    return _rotated_distance_m(grid, lat, lon)


def a1_corner_direction(grid: Grid) -> str:
    centre_u = grid.cols * grid.cell_m / 2
    centre_v = grid.rows * row_size_m(grid) / 2
    east, north = _uv_to_local(grid, -centre_u, -centre_v)
    bearing = math.degrees(math.atan2(east, north)) % 360.0
    return _COMPASS[round(bearing / 45.0) % 8]


def parse_grid(text: str) -> Grid:
    parts = (text or "").split(",")
    if len(parts) != 5:
        raise ValueError(
            "cuadrícula inválida: se esperaban 5 valores "
            "NORTH,WEST,CELL_M,COLS,ROWS"
        )
    stripped = [p.strip() for p in parts]
    try:
        north = float(stripped[0])
        west = float(stripped[1])
        cell_m = float(stripped[2])
        cols = int(stripped[3])
        rows = int(stripped[4])
    except ValueError as exc:
        raise ValueError(
            "cuadrícula inválida: NORTH,WEST,CELL_M deben ser números "
            "y COLS,ROWS enteros"
        ) from exc
    if cell_m <= 0:
        raise ValueError("cuadrícula inválida: CELL_M debe ser mayor que 0")
    if cols < 1 or cols > 26:
        raise ValueError("cuadrícula inválida: COLS debe estar entre 1 y 26")
    if rows < 1:
        raise ValueError("cuadrícula inválida: ROWS debe ser al menos 1")
    return Grid(north=north, west=west, cell_m=cell_m, cols=cols, rows=rows)


def _read_json(path) -> object:
    try:
        with open(path, encoding="utf-8-sig") as handle:
            return json.load(handle)
    except OSError as exc:
        raise ValueError(f"no se pudo leer la cuadrícula {path}: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise ValueError(f"cuadrícula inválida: {path} no es JSON válido ({exc})") from exc


def _number(data: dict, key: str) -> float:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"cuadrícula inválida: {key} debe ser un número")
    if not math.isfinite(value):
        raise ValueError(f"cuadrícula inválida: {key} debe ser un número finito")
    return float(value)


def _positive(data: dict, key: str) -> float:
    value = _number(data, key)
    if value <= 0:
        raise ValueError(f"cuadrícula inválida: {key} debe ser mayor que 0")
    return value


def _integer(data: dict, key: str) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"cuadrícula inválida: {key} debe ser un entero")
    return value


def _checked_labels(data: dict, cols: int) -> str:
    labels = data["labels"]
    if not isinstance(labels, str) or not (labels.isascii() and labels.isalpha()):
        raise ValueError("cuadrícula inválida: labels debe tener solo letras A-Z")
    if len(labels) != cols:
        raise ValueError(f"cuadrícula inválida: labels debe tener {cols} letras, una por columna")
    return labels.upper()


def _check_counts(cols: int, rows: int) -> None:
    if cols < 1 or cols > 26:
        raise ValueError("cuadrícula inválida: cols debe estar entre 1 y 26")
    if rows < 1:
        raise ValueError("cuadrícula inválida: rows debe ser al menos 1")


def _check_axes(col_bearing: float, row_bearing: float) -> None:
    # Near-parallel axes make the cell centre math divide by ~0.
    if abs(math.sin(math.radians(row_bearing - col_bearing))) < _MIN_AXIS_SINE:
        raise ValueError("cuadrícula inválida: los rumbos de columnas y filas no pueden ser paralelos")


def _grid_from_dict(data: object) -> Grid:
    if not isinstance(data, dict):
        raise ValueError("cuadrícula inválida: el archivo debe ser un objeto JSON")
    missing = [key for key in _FILE_KEYS if key not in data]
    if missing:
        raise ValueError("cuadrícula inválida: faltan " + ", ".join(missing))
    cols = _integer(data, "cols")
    rows = _integer(data, "rows")
    _check_counts(cols, rows)
    col_bearing = _number(data, "col_bearing_deg")
    row_bearing = _number(data, "row_bearing_deg")
    _check_axes(col_bearing, row_bearing)
    return Grid(north=_number(data, "north"), west=_number(data, "west"),
                cell_m=_positive(data, "cell_m"), cols=cols, rows=rows,
                row_m=_positive(data, "row_m"), col_bearing_deg=col_bearing,
                row_bearing_deg=row_bearing, labels=_checked_labels(data, cols))


def load_grid_file(path) -> Grid:
    return _grid_from_dict(_read_json(path))


def select_grid(text: str | None, path: str | None) -> Grid | None:
    if path is not None:
        return load_grid_file(path)
    if text is not None:
        return parse_grid(text)
    return None
