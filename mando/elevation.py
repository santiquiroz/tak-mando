"""DTED2 elevation reader with bilinear sampling and line of sight."""

import math
import struct
from dataclasses import dataclass

from mando.geo import haversine_m

_HDR = 3428
_REC_HEAD = 8
_REC_TAIL = 4


@dataclass
class Dem:
    lon0: float
    lat0: float
    dlon: float
    dlat: float
    ncols: int
    nrows: int
    data: bytes
    reclen: int


def _origin(field):
    deg = int(field[0:3])
    minutes = int(field[3:5])
    secs = int(field[5:7])
    v = deg + minutes / 60.0 + secs / 3600.0
    return -v if chr(field[7]) in ("W", "S") else v


def load_dted(path):
    with open(path, "rb") as f:
        data = bytes(f.read())
    try:
        if data[0:4] != b"UHL1":
            raise ValueError("bad magic")
        lon0 = _origin(data[4:12])
        lat0 = _origin(data[12:20])
        dlon = int(data[20:24]) / 36000.0
        dlat = int(data[24:28]) / 36000.0
        ncols = int(data[47:51])
        nrows = int(data[51:55])
        reclen = _REC_HEAD + 2 * nrows + _REC_TAIL
        if len(data) != _HDR + ncols * reclen:
            raise ValueError("bad size")
    except (ValueError, IndexError):
        raise ValueError(f"DTED inválido: {path}")
    return Dem(lon0, lat0, dlon, dlat, ncols, nrows, data, reclen)


def post(dem, col, row):
    if col < 0 or row < 0 or col >= dem.ncols or row >= dem.nrows:
        return None
    (v,) = struct.unpack_from(">H", dem.data, _HDR + col * dem.reclen + _REC_HEAD + 2 * row)
    if v == 0xFFFF:
        return None
    if v & 0x8000:
        mag = v & 0x7FFF
        return None if mag == 0x7FFF else -mag
    return v


def elevation(dem, lat, lon):
    fx = (lon - dem.lon0) / dem.dlon
    fy = (lat - dem.lat0) / dem.dlat
    c = math.floor(fx)
    r = math.floor(fy)
    v00 = post(dem, c, r)
    v10 = post(dem, c + 1, r)
    v01 = post(dem, c, r + 1)
    v11 = post(dem, c + 1, r + 1)
    if v00 is None or v10 is None or v01 is None or v11 is None:
        return None
    tx = fx - c
    ty = fy - r
    return (1.0 - tx) * (1.0 - ty) * v00 + tx * (1.0 - ty) * v10 + (1.0 - tx) * ty * v01 + tx * ty * v11


def line_of_sight(dem, a_lat, a_lon, a_height_m, b_lat, b_lon, b_height_m, step_m=10.0):
    dist = haversine_m(a_lat, a_lon, b_lat, b_lon)
    ea = elevation(dem, a_lat, a_lon)
    eb = elevation(dem, b_lat, b_lon)
    if ea is None or eb is None:
        return (None, None, dist)
    ha = ea + a_height_m
    hb = eb + b_height_m
    if step_m <= 0:
        step_m = 10.0
    d = step_m
    while d < dist:
        f = d / dist
        t = elevation(dem, a_lat + f * (b_lat - a_lat), a_lon + f * (b_lon - a_lon))
        if t is None:
            return (None, None, dist)
        if t - (ha + f * (hb - ha)) > 1.0:
            return (False, d, dist)
        d += step_m
    return (True, None, dist)
