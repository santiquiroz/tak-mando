"""Exposure grid and covered route by A*."""
import heapq
import json
import math
from dataclasses import dataclass


@dataclass
class Exposure:
    north: float
    west: float
    cell_m: float
    rows: int
    cols: int
    count: list[list[int]]
    dlat: float = 0.0
    dlon: float = 0.0

    def __post_init__(self):
        self.dlat = self.cell_m / 110574.0
        self.dlon = self.cell_m / (111320.0 * math.cos(math.radians(self.north - self.rows * self.dlat / 2)))


def load_exposure(path):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    try:
        north = doc["north"]
        west = doc["west"]
        cell_m = doc["cell_m"]
        rows = doc["rows"]
        cols = doc["cols"]
        count = doc["count"]
    except (KeyError, TypeError):
        raise ValueError("bad exposure doc")
    if isinstance(north, bool) or not isinstance(north, (int, float)):
        raise ValueError("bad exposure doc")
    if isinstance(west, bool) or not isinstance(west, (int, float)):
        raise ValueError("bad exposure doc")
    if isinstance(cell_m, bool) or not isinstance(cell_m, (int, float)) or cell_m <= 0:
        raise ValueError("bad exposure doc")
    if isinstance(rows, bool) or not isinstance(rows, int) or rows <= 0:
        raise ValueError("bad exposure doc")
    if isinstance(cols, bool) or not isinstance(cols, int) or cols <= 0:
        raise ValueError("bad exposure doc")
    if not isinstance(count, list) or len(count) != rows:
        raise ValueError("bad exposure doc")
    for row in count:
        if not isinstance(row, list) or len(row) != cols:
            raise ValueError("bad exposure doc")
    return Exposure(float(north), float(west), float(cell_m), rows, cols, count)


def cell_of(e, lat, lon):
    row = math.floor((e.north - lat) / e.dlat)
    col = math.floor((lon - e.west) / e.dlon)
    if 0 <= row < e.rows and 0 <= col < e.cols:
        return (row, col)
    return None


def center_of(e, row, col):
    return (e.north - (row + 0.5) * e.dlat, e.west + (col + 0.5) * e.dlon)


def _heuristic(e, cell, goal):
    return math.hypot(goal[0] - cell[0], goal[1] - cell[1]) * e.cell_m


def _neighbors(e, cell):
    diag = math.sqrt(2.0)
    out = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            nr = cell[0] + dr
            nc = cell[1] + dc
            if 0 <= nr < e.rows and 0 <= nc < e.cols:
                base = diag if dr != 0 and dc != 0 else 1.0
                out.append((nr, nc, base))
    return out


def _astar(e, start, goal):
    openh = []
    counter = 0
    heapq.heappush(openh, (_heuristic(e, start, goal), counter, start))
    came = {}
    cost = {start: 0.0}
    closed = set()
    while openh:
        _, _, cur = heapq.heappop(openh)
        if cur in closed:
            continue
        closed.add(cur)
        if cur == goal:
            cells = [cur]
            while cells[-1] != start:
                cells.append(came[cells[-1]])
            cells.reverse()
            return cells
        for nr, nc, base in _neighbors(e, cur):
            nxt = (nr, nc)
            if nxt in closed:
                continue
            step = base * e.cell_m * (1 + 4 * e.count[nr][nc])
            ng = cost[cur] + step
            if ng < cost.get(nxt, math.inf):
                cost[nxt] = ng
                came[nxt] = cur
                counter += 1
                heapq.heappush(openh, (ng + _heuristic(e, nxt, goal), counter, nxt))
    return None


def _subsample(path):
    n = len(path)
    if n <= 20:
        return path
    return [path[(i * (n - 1)) // 19] for i in range(20)]


def covered_route(e, a_lat, a_lon, b_lat, b_lon):
    start = cell_of(e, a_lat, a_lon)
    goal = cell_of(e, b_lat, b_lon)
    if start is None or goal is None:
        return None
    if start == goal:
        a = (a_lat, a_lon)
        b = (b_lat, b_lon)
        return [a] if a == b else [a, b]
    cells = _astar(e, start, goal)
    if cells is None:
        return None
    path = [center_of(e, r, c) for r, c in cells]
    path[0] = (a_lat, a_lon)
    path[-1] = (b_lat, b_lon)
    return _subsample(path)


def exposed_fraction(e, path):
    if not path:
        return 0.0
    n = 0
    for lat, lon in path:
        cell = cell_of(e, lat, lon)
        if cell is not None and e.count[cell[0]][cell[1]] > 0:
            n += 1
    return n / len(path)
