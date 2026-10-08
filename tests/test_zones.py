import json

import pytest

from mando.geo import format_distance
from mando.zones import (
    Place,
    describe_location,
    load_places,
    load_zones,
    nearest_place,
)


def _write(tmp_path, obj):
    path = tmp_path / "field.geojson"
    path.write_text(json.dumps(obj), encoding="utf-8")
    return str(path)


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _poly(name, ring, folder=None, alert=None, description=None):
    props = {"name": name}
    if folder is not None:
        props["folder"] = folder
    if alert is not None:
        props["alert"] = alert
    if description is not None:
        props["description"] = description
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


SQUARE = [[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]]


def test_peligros_message_prefers_alert(tmp_path):
    path = _write(tmp_path, _fc([
        _poly("Tanque", SQUARE, folder="Peligros", alert="No entrar", description="desc"),
        _poly("Borde", SQUARE, folder="Peligros", description="Cuidado"),
        _poly("Vacio", SQUARE, folder="Peligros"),
    ]))
    zones = {z.name: z for z in load_zones(path)}
    assert zones["Tanque"].message == "No entrar"
    assert zones["Borde"].message == "Cuidado"
    assert zones["Vacio"].message == ""


def test_alert_outside_folder_is_zone(tmp_path):
    path = _write(tmp_path, _fc([
        _poly("Fuera", SQUARE, folder="Otros"),
        _poly("ConAlerta", SQUARE, folder="Otros", alert="Peligro!"),
        _poly("SinFolder", SQUARE, alert="Ojo"),
    ]))
    zones = {z.name: z for z in load_zones(path)}
    assert "Fuera" not in zones
    assert zones["ConAlerta"].message == "Peligro!"
    assert zones["SinFolder"].message == "Ojo"


def test_load_places_skips_lines_and_curvas(tmp_path):
    path = _write(tmp_path, _fc([
        {
            "type": "Feature",
            "properties": {"name": "Silo", "folder": "Lugares"},
            "geometry": {"type": "Point", "coordinates": [0.005, 0.005]},
        },
        _poly("Bloque", SQUARE, folder="Lugares"),
        {
            "type": "Feature",
            "properties": {"name": "Camino", "folder": "Lugares"},
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        },
        {
            "type": "Feature",
            "properties": {"name": "Curva1", "folder": "Curvas de nivel"},
            "geometry": {"type": "Point", "coordinates": [0.1, 0.1]},
        },
        _poly("CurvaPoly", SQUARE, folder="Curvas"),
        {
            "type": "Feature",
            "properties": {"folder": "Lugares"},
            "geometry": {"type": "Point", "coordinates": [0.2, 0.2]},
        },
    ]))
    places = {p.name: p for p in load_places(path)}
    assert set(places) == {"Silo", "Bloque"}
    assert places["Silo"].ring is None
    assert places["Silo"].lat == pytest.approx(0.005)
    assert places["Bloque"].ring == SQUARE
    assert places["Bloque"].lat == pytest.approx(0.005)


def test_nearest_place_prefers_smallest_container():
    big = Place(name="Grande", lat=0.005, lon=0.005, ring=SQUARE)
    small_ring = [[0.002, 0.002], [0.004, 0.002], [0.004, 0.004], [0.002, 0.004]]
    small = Place(name="Peque", lat=0.003, lon=0.003, ring=small_ring)
    found, dist = nearest_place([big, small], 0.003, 0.003)
    assert found.name == "Peque"
    assert dist == 0


def test_nearest_place_otherwise_nearest():
    far = Place(name="Lejos", lat=0.05, lon=0.05, ring=None)
    near = Place(name="Cerca", lat=0.001, lon=0.0, ring=None)
    found, dist = nearest_place([far, near], 0.0, 0.0)
    assert found.name == "Cerca"
    assert dist > 0
    poly = Place(name="Poli", lat=0.005, lon=0.005, ring=SQUARE)
    found2, _ = nearest_place([far, poly], 0.011, 0.005)
    assert found2.name == "Poli"


def test_describe_location():
    inside = Place(name="Bloque central", lat=0.005, lon=0.005, ring=SQUARE)
    assert describe_location([inside], 0.005, 0.005) == "en Bloque central"
    point = Place(name="Silo 2", lat=0.0, lon=0.0, ring=None)
    text = describe_location([point], 0.0004, 0.0)
    assert text.startswith("a ")
    assert text.endswith(" de Silo 2")
    assert "m" in text
    assert describe_location([point], 0.05, 0.05) == ""
    assert describe_location([], 0.0, 0.0) == ""


def test_describe_location_uses_format_distance():
    point = Place(name="X", lat=0.0, lon=0.0, ring=None)
    text = describe_location([point], 0.0004, 0.0)
    assert text == f"a {format_distance(nearest_place([point], 0.0004, 0.0)[1])} de X"


def test_invalid_json_raises(tmp_path):
    bad = tmp_path / "bad.geojson"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_zones(str(bad))
    with pytest.raises(ValueError):
        load_places(str(bad))
