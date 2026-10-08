from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Grid:
    north: float
    west: float
    cell_m: float
    cols: int
    rows: int


def cell_deg(grid: Grid) -> tuple[float, float]:
    dlat = grid.cell_m / 110574.0
    ref_lat = grid.north - grid.rows * dlat / 2
    dlon = grid.cell_m / (111320.0 * math.cos(math.radians(ref_lat)))
    return (dlat, dlon)


def grid_ref(grid: Grid, lat: float, lon: float) -> str | None:
    if lat is None or lon is None:
        return None
    dlat, dlon = cell_deg(grid)
    col = math.floor((lon - grid.west) / dlon)
    row = math.floor((grid.north - lat) / dlat)
    if col < 0 or col >= grid.cols or row < 0 or row >= grid.rows:
        return None
    return f"{chr(65 + col)}{row + 1}"


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
