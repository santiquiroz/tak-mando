import json

import pytest

from mando.exposure import Exposure, cell_of, center_of, covered_route, exposed_fraction, load_exposure


def _exp(tmp_path, count):
    doc = {"north": 5.17, "west": -75.5, "cell_m": 10, "rows": len(count), "cols": len(count[0]), "count": count}
    p = tmp_path / "e.json"; p.write_text(json.dumps(doc), encoding="utf-8")
    return load_exposure(p)


def test_avoids_exposed_band(tmp_path):
    rows, cols = 20, 20
    count = [[0] * cols for _ in range(rows)]
    for r in range(0, 15):
        count[r][10] = 3
    e = _exp(tmp_path, count)
    a = center_of(e, 2, 2); b = center_of(e, 2, 17)
    path = covered_route(e, *a, *b)
    assert path[0] == a and path[-1] == b and len(path) <= 20
    cells = [cell_of(e, *p) for p in path]
    assert min(r for r, _ in cells) >= 0
    assert exposed_fraction(e, path) <= 0.1


def test_outside_and_no_path(tmp_path):
    count = [[0] * 5 for _ in range(5)]
    e = _exp(tmp_path, count)
    assert covered_route(e, 0.0, 0.0, *center_of(e, 1, 1)) is None


def test_bad_doc(tmp_path):
    p = tmp_path / "e.json"; p.write_text('{"north": 5}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_exposure(p)


@pytest.mark.parametrize("bad", ["1", None, -1, True])
def test_invalid_cell_values_rejected(tmp_path, bad):
    doc = {"north": 5.17, "west": -75.5, "cell_m": 10, "rows": 2, "cols": 2,
           "count": [[0, 0], [0, bad]]}
    p = tmp_path / "e.json"; p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError) as err:
        load_exposure(p)
    msg = str(err.value)
    assert "Exposición inválida" in msg
    assert str(p) in msg


def test_valid_grid_still_loads(tmp_path):
    e = _exp(tmp_path, [[0, 1], [2, 0]])
    assert e.count == [[0, 1], [2, 0]]
