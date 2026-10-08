import json
from datetime import datetime, timedelta, timezone

import pytest

from mando.geo import haversine_m
from mando.layer import (
    FOLDER_GAME,
    FOLDER_PROPOSALS,
    Layer,
    LayerError,
    circle,
    normalize,
)

NOW = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)


def _layer(tmp_path):
    return Layer(tmp_path / "juego.geojson", tmp_path / "state.json")


def _point(lat=5.16, lon=-75.49):
    return {"type": "Point", "coordinates": [lon, lat]}


def _add(layer, name, folder=FOLDER_GAME, uid="u1", geometry=None):
    return layer.add_feature(
        {"name": name, "folder": folder}, geometry or _point(), NOW, uid
    )


def test_empty_when_files_missing(tmp_path):
    layer = _layer(tmp_path)
    assert layer.features() == []
    assert layer.authorized() == set()
    assert layer.proposals() == []
    assert layer.mtime() == 0.0


def test_add_assigns_ids_and_writes_geojson(tmp_path):
    layer = _layer(tmp_path)
    a = _add(layer, "EXFIL")
    b = _add(layer, "MED")
    assert a["properties"]["id"] == "j-1"
    assert b["properties"]["id"] == "j-2"
    assert a["properties"]["created"] == "2026-10-10T22:00:00Z"
    assert a["properties"]["author_uid"] == "u1"
    data = json.loads((tmp_path / "juego.geojson").read_text(encoding="utf-8"))
    assert data["type"] == "FeatureCollection"
    assert [f["properties"]["name"] for f in data["features"]] == ["EXFIL", "MED"]
    assert layer.mtime() > 0


def test_ids_never_reused(tmp_path):
    layer = _layer(tmp_path)
    _add(layer, "A")
    layer.delete_feature("j-1", "u1")
    assert _add(layer, "B")["properties"]["id"] == "j-2"


def test_update_and_move_point(tmp_path):
    layer = _layer(tmp_path)
    _add(layer, "A")
    layer.update_feature("j-1", {"name": "B", "id": "x"}, NOW + timedelta(minutes=1), "u1")
    moved = layer.move_feature("j-1", 5.1601, -75.4901, NOW, "u1")
    assert moved["properties"]["name"] == "B"
    assert moved["properties"]["id"] == "j-1"
    assert moved["geometry"]["coordinates"] == [-75.4901, 5.1601]


def test_move_polygon_translates_centroid(tmp_path):
    layer = _layer(tmp_path)
    ring = circle(5.16, -75.49, 50)
    _add(layer, "Z", geometry={"type": "Polygon", "coordinates": [ring]})
    moved = layer.move_feature("j-1", 5.161, -75.491, NOW, "u1")
    new_ring = moved["geometry"]["coordinates"][0]
    lats = [p[1] for p in new_ring[:-1]]
    lons = [p[0] for p in new_ring[:-1]]
    assert abs(sum(lats) / len(lats) - 5.161) < 1e-6
    assert abs(sum(lons) / len(lons) - (-75.491)) < 1e-6
    assert new_ring[0] == new_ring[-1]


def test_unknown_id_raises(tmp_path):
    with pytest.raises(LayerError):
        _layer(tmp_path).update_feature("j-99", {}, NOW, "u1")


def test_find_by_id_name_accents_and_prefix(tmp_path):
    layer = _layer(tmp_path)
    _add(layer, "EXFIL Alfa")
    _add(layer, "Médico")
    assert layer.find("j-1")["properties"]["name"] == "EXFIL Alfa"
    assert layer.find("exfil alfa")["properties"]["id"] == "j-1"
    assert layer.find("medico")["properties"]["id"] == "j-2"
    assert layer.find("exfil")["properties"]["id"] == "j-1"


def test_find_ambiguous_returns_none(tmp_path):
    layer = _layer(tmp_path)
    _add(layer, "EXFIL Alfa")
    _add(layer, "EXFIL Bravo")
    assert layer.find("exfil") is None
    assert [f["properties"]["id"] for f in layer.matches("exfil")] == ["j-1", "j-2"]


def test_undo_update_then_creation(tmp_path):
    layer = _layer(tmp_path)
    _add(layer, "A")
    layer.update_feature("j-1", {"name": "B"}, NOW, "u1")
    assert layer.undo_last("u1").startswith("Deshecho")
    assert layer.get("j-1")["properties"]["name"] == "A"
    assert layer.undo_last("u1").startswith("Deshecho")
    assert layer.get("j-1") is None
    assert layer.undo_last("u1") == "Nada que deshacer."


def test_undo_delete_restores(tmp_path):
    layer = _layer(tmp_path)
    _add(layer, "A")
    layer.delete_feature("j-1", "u1")
    layer.undo_last("u1")
    assert layer.get("j-1")["properties"]["name"] == "A"


def test_undo_only_own_history(tmp_path):
    layer = _layer(tmp_path)
    _add(layer, "A", uid="u1")
    assert layer.undo_last("u2") == "Nada que deshacer."
    assert layer.get("j-1") is not None


def test_history_capped_at_100(tmp_path):
    layer = _layer(tmp_path)
    for i in range(105):
        _add(layer, f"P{i}")
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert len(state["history"]) == 100


def test_proposals_roundtrip(tmp_path):
    layer = _layer(tmp_path)
    n1 = layer.add_proposal("marcar_punto", {"nombre": "X"}, "Recon", "u2", NOW, "marcar X", fid="j-1")
    n2 = layer.add_proposal("borrar", {"objeto": "j-1"}, "Recon", "u2", NOW, "borrar j-1")
    assert (n1, n2) == (1, 2)
    assert layer.proposals()[0]["notified"] is False
    layer.mark_notified(1)
    assert layer.proposals()[0]["notified"] is True
    popped = layer.pop_proposal(1)
    assert popped["op"] == "marcar_punto" and popped["fid"] == "j-1"
    assert layer.pop_proposal(1) is None


def test_authorized_persists_across_instances(tmp_path):
    _layer(tmp_path).authorize("u1")
    assert _layer(tmp_path).authorized() == {"u1"}
    _layer(tmp_path).revoke("u1")
    assert _layer(tmp_path).authorized() == set()


def test_announcements_due_once(tmp_path):
    layer = _layer(tmp_path)
    layer.add_announcement(NOW + timedelta(minutes=5), "cierra", "todos")
    assert layer.due_announcements(NOW) == []
    due = layer.due_announcements(NOW + timedelta(minutes=5))
    assert [(d["text"], d["audience"]) for d in due] == [("cierra", "todos")]
    assert layer.due_announcements(NOW + timedelta(minutes=6)) == []


def test_corrupt_layer_raises_and_is_not_overwritten(tmp_path):
    path = tmp_path / "juego.geojson"
    path.write_text("{roto", encoding="utf-8")
    layer = _layer(tmp_path)
    with pytest.raises(LayerError):
        layer.features()
    with pytest.raises(LayerError):
        _add(layer, "A")
    assert path.read_text(encoding="utf-8") == "{roto"


def test_circle_closed_with_radius():
    ring = circle(5.16, -75.49, 100, n=24)
    assert len(ring) == 25
    assert ring[0] == ring[-1]
    for lon, lat in ring:
        assert abs(haversine_m(5.16, -75.49, lat, lon) - 100) < 1.0


def test_normalize():
    assert normalize("  Médico   NORTE ") == "medico norte"
