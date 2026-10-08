from __future__ import annotations

import json
from dataclasses import dataclass

from mando.geo import (
    centroid,
    distance_to_ring_m,
    format_distance,
    haversine_m,
    point_in_ring,
)


@dataclass
class Zone:
    name: str
    ring: list
    message: str


@dataclass
class Place:
    name: str
    lat: float
    lon: float
    ring: list | None


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {path}") from exc


def _features(data):
    if isinstance(data, dict):
        feats = data.get("features")
        if isinstance(feats, list):
            return feats
        if data.get("type") == "Feature":
            return [data]
        return []
    if isinstance(data, list):
        return data
    return []


def _str(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _non_empty(value):
    if isinstance(value, str):
        return value.strip() != ""
    return bool(value)


def _name(props):
    name = props.get("name") or props.get("title") or ""
    return _str(name)


def load_zones(path, alert_folders=("Peligros",)):
    data = _load_json(path)
    zones = []
    for feat in _features(data):
        if not isinstance(feat, dict):
            continue
        geom = feat.get("geometry") or {}
        if not isinstance(geom, dict) or geom.get("type") != "Polygon":
            continue
        coords = geom.get("coordinates")
        if not coords or not coords[0]:
            continue
        props = feat.get("properties") or {}
        if not isinstance(props, dict):
            props = {}
        folder = props.get("folder")
        alert = props.get("alert")
        if folder not in alert_folders and not _non_empty(alert):
            continue
        if _non_empty(alert):
            message = _str(alert)
        elif _non_empty(props.get("description")):
            message = _str(props.get("description"))
        else:
            message = ""
        zones.append(Zone(name=_name(props), ring=coords[0], message=message))
    return zones


def load_places(path):
    data = _load_json(path)
    places = []
    for feat in _features(data):
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        if not isinstance(props, dict):
            props = {}
        folder = props.get("folder") or ""
        if isinstance(folder, str) and folder.startswith("Curvas"):
            continue
        geom = feat.get("geometry") or {}
        if not isinstance(geom, dict):
            continue
        gtype = geom.get("type")
        if gtype in ("LineString", "MultiLineString"):
            continue
        name = _name(props)
        if not _non_empty(name):
            continue
        if gtype == "Point":
            coords = geom.get("coordinates")
            if not coords or len(coords) < 2:
                continue
            try:
                lon = float(coords[0])
                lat = float(coords[1])
            except (TypeError, ValueError):
                continue
            places.append(Place(name=name, lat=lat, lon=lon, ring=None))
        elif gtype == "Polygon":
            coords = geom.get("coordinates")
            if not coords or not coords[0]:
                continue
            ring = coords[0]
            try:
                lat, lon = centroid(ring)
            except (ZeroDivisionError, TypeError, KeyError, IndexError):
                continue
            places.append(Place(name=name, lat=lat, lon=lon, ring=ring))
    return places


def _bbox_area(ring):
    lons = [v[0] for v in ring]
    lats = [v[1] for v in ring]
    return (max(lons) - min(lons)) * (max(lats) - min(lats))


def nearest_place(places, lat, lon):
    if not places:
        return None
    containing = [p for p in places if p.ring is not None and point_in_ring(lat, lon, p.ring)]
    if containing:
        best = min(containing, key=lambda p: _bbox_area(p.ring))
        return (best, 0.0)
    best = None
    best_d = None
    for p in places:
        if p.ring is not None:
            d = distance_to_ring_m(lat, lon, p.ring)
        else:
            d = haversine_m(lat, lon, p.lat, p.lon)
        if best_d is None or d < best_d:
            best = p
            best_d = d
    if best is None:
        return None
    return (best, best_d)


def describe_location(places, lat, lon):
    found = nearest_place(places, lat, lon)
    if found is None:
        return ""
    place, dist = found
    if dist == 0:
        return f"en {place.name}"
    if dist < 150:
        return f"a {format_distance(dist)} de {place.name}"
    return ""
