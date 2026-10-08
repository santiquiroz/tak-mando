"""Shared game layer and state with file lock and atomic writes."""

import contextlib
import copy
import json
import math
import os
import tempfile
import unicodedata
from datetime import timezone
from pathlib import Path

from mando.geo import centroid

try:
    import fcntl
except ImportError:  # Windows: tests run here; production runs on Linux.
    fcntl = None
    import msvcrt

FOLDER_GAME = "Juego"
FOLDER_PROPOSALS = "Propuestas"

COLORS = {"peligro": "#ff3b30", "objetivo:libre": "#ffffff", "objetivo:nuestro": "#34c759", "objetivo:enemigo": "#ff3b30", "objetivo:disputado": "#ffcc00", "reunion": "#32d2ff", "medico": "#ff2d55", "spawn": "#5856d6", "enemigo": "#ff9500", "info": "#8e8e93", "zona": "#32d2ff", "propuesta": "#ff9500"}

_UNDO_WORD = {"add": "creación", "update": "modificación", "move": "movimiento", "delete": "eliminación"}

_DEFAULT_STATE = {"next_id": 1, "next_proposal": 1, "next_announcement": 1, "authorized": [], "proposals": [], "announcements": [], "history": []}


class LayerError(ValueError):
    pass


@contextlib.contextmanager
def _locked(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as f:
        _acquire(f)
        try:
            yield
        finally:
            _release(f)


def _acquire(f) -> None:
    if fcntl is not None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        return
    f.seek(0)
    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)


def _release(f) -> None:
    if fcntl is not None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return
    f.seek(0)
    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def write_json_atomic(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


_write_json_atomic = write_json_atomic


def circle(lat, lon, radius_m, n=24):
    dlat = radius_m / 110574.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat)))
    ring = [
        [lon + dlon * math.sin(2 * math.pi * i / n), lat + dlat * math.cos(2 * math.pi * i / n)]
        for i in range(n)
    ]
    return ring + [list(ring[0])]


def normalize(text) -> str:
    plain = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return " ".join(plain.lower().split())


def _name_of(feat, fallback):
    if isinstance(feat, dict):
        return feat.get("properties", {}).get("name", fallback)
    return fallback


class Layer:
    def __init__(self, path, state_path, lock_path=None):
        self._path = Path(path)
        self._state_path = Path(state_path)
        if lock_path is None:
            self._lock_path = Path(str(state_path) + ".lock")
        else:
            self._lock_path = Path(lock_path)

    def features(self, folder=None):
        feats = self._read_layer()["features"]
        if folder is None:
            return list(feats)
        return [f for f in feats if f.get("properties", {}).get("folder") == folder]

    def get(self, fid):
        for feat in self._read_layer()["features"]:
            if feat.get("properties", {}).get("id") == fid:
                return feat
        return None

    def matches(self, ref, folder=None):
        feats = self._ordered(folder)
        for feat in feats:
            if feat.get("properties", {}).get("id") == ref:
                return [feat]
        target = normalize(ref)
        exact = [f for f in feats if normalize(_name_of(f, "")) == target]
        if exact:
            return exact
        return [f for f in feats if normalize(_name_of(f, "")).startswith(target)]

    def find(self, ref, folder=None):
        found = self.matches(ref, folder)
        if len(found) == 1:
            return found[0]
        return None

    def _ordered(self, folder):
        feats = self._read_layer()["features"]
        if folder is not None:
            return [f for f in feats if f.get("properties", {}).get("folder") == folder]
        game = [f for f in feats if f.get("properties", {}).get("folder") == FOLDER_GAME]
        prop = [f for f in feats if f.get("properties", {}).get("folder") == FOLDER_PROPOSALS]
        rest = [f for f in feats if f.get("properties", {}).get("folder") not in (FOLDER_GAME, FOLDER_PROPOSALS)]
        return game + prop + rest

    def add_feature(self, props, geometry, now, actor_uid):
        with _locked(self._lock_path):
            layer = self._read_layer()
            state = self._read_state()
            fid = f"j-{state['next_id']}"
            state["next_id"] += 1
            stamp = self._stamp(now)
            properties = dict(props)
            properties["id"] = fid
            properties["created"] = stamp
            properties["updated"] = stamp
            properties["author_uid"] = actor_uid
            feat = {"type": "Feature", "properties": properties, "geometry": geometry}
            layer["features"].append(feat)
            self._record(state, actor_uid, "add", fid, None)
            self._write(layer, state)
            return feat

    def update_feature(self, fid, changes, now, actor_uid):
        with _locked(self._lock_path):
            layer = self._read_layer()
            state = self._read_state()
            feat = self._require(layer, fid)
            before = copy.deepcopy(feat)
            for key, value in changes.items():
                if key != "id":
                    feat["properties"][key] = value
            feat["properties"]["updated"] = self._stamp(now)
            self._record(state, actor_uid, "update", fid, before)
            self._write(layer, state)
            return feat

    def move_feature(self, fid, lat, lon, now, actor_uid):
        with _locked(self._lock_path):
            layer = self._read_layer()
            state = self._read_state()
            feat = self._require(layer, fid)
            before = copy.deepcopy(feat)
            geometry = feat.get("geometry", {})
            if geometry.get("type") == "Polygon":
                self._move_polygon(geometry, lat, lon)
            else:
                geometry["coordinates"] = [lon, lat]
            feat["properties"]["updated"] = self._stamp(now)
            self._record(state, actor_uid, "move", fid, before)
            self._write(layer, state)
            return feat

    @staticmethod
    def _move_polygon(geometry, lat, lon):
        rings = geometry.get("coordinates", [])
        if not rings:
            return
        old_lat, old_lon = centroid(rings[0])
        dlat = lat - old_lat
        dlon = lon - old_lon
        geometry["coordinates"] = [[[x + dlon, y + dlat] for x, y in ring] for ring in rings]

    def delete_feature(self, fid, actor_uid):
        with _locked(self._lock_path):
            layer = self._read_layer()
            state = self._read_state()
            feats = layer["features"]
            for i, feat in enumerate(feats):
                if feat.get("properties", {}).get("id") == fid:
                    before = copy.deepcopy(feat)
                    del feats[i]
                    self._record(state, actor_uid, "delete", fid, before)
                    self._write(layer, state)
                    return before
            raise LayerError(f"no existe {fid}")

    def undo_last(self, actor_uid):
        with _locked(self._lock_path):
            layer = self._read_layer()
            state = self._read_state()
            entry = self._last_own(state, actor_uid)
            if entry is None:
                return "Nada que deshacer."
            name = self._apply_undo(layer, entry)
            entry["undone"] = True
            self._write(layer, state)
            word = _UNDO_WORD.get(entry["action"], entry["action"])
            return f"Deshecho: {word} de {name} ({entry['fid']})."

    @staticmethod
    def _last_own(state, uid):
        for entry in reversed(state["history"]):
            if entry.get("uid") == uid and not entry.get("undone"):
                return entry
        return None

    @staticmethod
    def _apply_undo(layer, entry):
        feats = layer["features"]
        fid = entry["fid"]
        index = None
        current = None
        for i, feat in enumerate(feats):
            if feat.get("properties", {}).get("id") == fid:
                index = i
                current = feat
                break
        if entry["action"] == "add":
            if index is not None:
                del feats[index]
            return _name_of(current, fid)
        before = copy.deepcopy(entry.get("before"))
        if before is None:
            return _name_of(current, fid)
        if index is None:
            feats.append(before)
        else:
            feats[index] = before
        return _name_of(before, fid)

    def authorized(self):
        return set(self._read_state()["authorized"])

    def authorize(self, uid):
        with _locked(self._lock_path):
            layer = self._read_layer()
            state = self._read_state()
            if uid not in state["authorized"]:
                state["authorized"].append(uid)
            self._write(layer, state)

    def revoke(self, uid):
        with _locked(self._lock_path):
            layer = self._read_layer()
            state = self._read_state()
            state["authorized"] = [u for u in state["authorized"] if u != uid]
            self._write(layer, state)

    def add_proposal(self, op, args, author, author_uid, now, summary, fid=None):
        with _locked(self._lock_path):
            layer = self._read_layer()
            state = self._read_state()
            n = state["next_proposal"]
            state["next_proposal"] += 1
            state["proposals"].append({
                "n": n, "op": op, "args": args, "author": author,
                "author_uid": author_uid, "created": self._stamp(now),
                "summary": summary, "fid": fid, "notified": False,
            })
            self._write(layer, state)
            return n

    def proposals(self):
        return list(self._read_state()["proposals"])

    def pop_proposal(self, n):
        with _locked(self._lock_path):
            layer = self._read_layer()
            state = self._read_state()
            for i, prop in enumerate(state["proposals"]):
                if prop.get("n") == n:
                    found = state["proposals"].pop(i)
                    self._write(layer, state)
                    return found
            return None

    def mark_notified(self, n):
        with _locked(self._lock_path):
            layer = self._read_layer()
            state = self._read_state()
            for prop in state["proposals"]:
                if prop.get("n") == n:
                    prop["notified"] = True
                    self._write(layer, state)
                    return

    def add_announcement(self, at_utc, text, audience):
        with _locked(self._lock_path):
            layer = self._read_layer()
            state = self._read_state()
            n = state["next_announcement"]
            state["next_announcement"] += 1
            state["announcements"].append({
                "n": n, "at": self._stamp(at_utc), "text": text, "audience": audience,
            })
            self._write(layer, state)
            return n

    def due_announcements(self, now):
        with _locked(self._lock_path):
            layer = self._read_layer()
            state = self._read_state()
            mark = self._stamp(now)
            due = [a for a in state["announcements"] if a.get("at", "") <= mark]
            if due:
                state["announcements"] = [a for a in state["announcements"] if a.get("at", "") > mark]
                self._write(layer, state)
            return due

    def mtime(self):
        try:
            return self._path.stat().st_mtime
        except OSError:
            return 0.0

    def _read_layer(self):
        if not self._path.exists():
            return {"type": "FeatureCollection", "features": []}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise LayerError(f"capa dañada: {self._path}")
        if not isinstance(data, dict) or not isinstance(data.get("features"), list):
            raise LayerError(f"capa dañada: {self._path}")
        return data

    def _read_state(self):
        if not self._state_path.exists():
            return copy.deepcopy(_DEFAULT_STATE)
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise LayerError(f"estado dañado: {self._state_path}")
        if not isinstance(data, dict):
            raise LayerError(f"estado dañado: {self._state_path}")
        fresh = copy.deepcopy(_DEFAULT_STATE)
        fresh.update(data)
        return fresh

    def _write(self, layer, state):
        write_json_atomic(self._path, layer)
        write_json_atomic(self._state_path, state)

    @staticmethod
    def _record(state, uid, action, fid, before):
        state["history"].append({"uid": uid, "action": action, "fid": fid, "before": before, "undone": False})
        state["history"] = state["history"][-100:]

    @staticmethod
    def _stamp(now):
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _require(layer, fid):
        for feat in layer["features"]:
            if feat.get("properties", {}).get("id") == fid:
                return feat
        raise LayerError(f"no existe {fid}")
