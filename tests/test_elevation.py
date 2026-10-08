import struct
from mando.elevation import Dem, elevation, line_of_sight, load_dted, post


def _sm(v):
    return 0xFFFF if v is None else (v if v >= 0 else 0x8000 | -v)


def _write_dted(path, heights, lon0=-75.5, lat0=5.15, n=37):
    uhl = bytearray(b" " * 80)
    uhl[0:4] = b"UHL1"; uhl[4:12] = b"0753000W"; uhl[12:20] = b"0050900N"
    uhl[20:24] = b"0010"; uhl[24:28] = b"0010"; uhl[47:51] = f"{n:04d}".encode(); uhl[51:55] = f"{n:04d}".encode()
    body = bytearray(uhl) + b" " * 648 + b" " * 2700
    for c in range(n):
        rec = bytes([0xAA]) + c.to_bytes(3, "big") + c.to_bytes(2, "big") + (0).to_bytes(2, "big")
        rec += b"".join(struct.pack(">H", _sm(heights(c, r))) for r in range(n)) + b"\0\0\0\0"
        body += rec
    path.write_bytes(bytes(body))


def test_reads_header_and_posts(tmp_path):
    p = tmp_path / "n05.dt2"
    _write_dted(p, lambda c, r: 2000 + r)
    dem = load_dted(p)
    assert (dem.ncols, dem.nrows) == (37, 37)
    assert abs(dem.lon0 - (-75.5)) < 1e-9 and abs(dem.lat0 - 5.15) < 1e-9
    assert post(dem, 0, 0) == 2000 and post(dem, 3, 10) == 2010


def test_negative_and_void(tmp_path):
    p = tmp_path / "n05.dt2"
    _write_dted(p, lambda c, r: None if c == 5 else -12)
    dem = load_dted(p)
    assert post(dem, 1, 1) == -12 and post(dem, 5, 1) is None
    assert elevation(dem, 5.15 + 1 / 3600, -75.5 + 5 / 3600) is None


def test_bilinear_and_outside(tmp_path):
    p = tmp_path / "n05.dt2"
    _write_dted(p, lambda c, r: 2000 + 10 * r)
    dem = load_dted(p)
    assert abs(elevation(dem, 5.15 + 1.5 / 3600, -75.5 + 2 / 3600) - 2015) < 1e-6
    assert elevation(dem, 6.0, -75.5) is None


def test_line_of_sight_blocked_by_ridge(tmp_path):
    p = tmp_path / "n05.dt2"
    _write_dted(p, lambda c, r: 2100 if c == 18 else 2000)
    dem = load_dted(p)
    vis, blocked, dist = line_of_sight(dem, 5.155, -75.5 + 10 / 3600, 1.7, 5.155, -75.5 + 26 / 3600, 1.7)
    assert vis is False and 200 < blocked < 300 and 450 < dist < 520
    vis, blocked, _ = line_of_sight(dem, 5.155, -75.5 + 10 / 3600, 300, 5.155, -75.5 + 26 / 3600, 1.7)
    assert vis is True and blocked is None


def test_bad_file(tmp_path):
    import pytest
    p = tmp_path / "x.dt2"; p.write_bytes(b"nope")
    with pytest.raises(ValueError):
        load_dted(p)
