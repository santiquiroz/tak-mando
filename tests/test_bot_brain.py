import json
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone

import pytest

from mando import cot
from mando.bot import Bot
from mando.brain import LlmError
from mando.grid import Grid
from mando.layer import Layer, circle
from mando.zones import Zone

NOW = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)
GRID = Grid(north=5.1650, west=-75.4960, cell_m=100, cols=9, rows=9)


class SyncExecutor:
    def submit(self, fn, *args):
        f = Future()
        try:
            f.set_result(fn(*args))
        except Exception as exc:
            f.set_exception(exc)
        return f


class ManualExecutor:
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        f = Future()
        self.jobs.append((f, fn, args))
        return f

    def finish(self):
        for f, fn, args in self.jobs:
            f.set_result(fn(*args))


class FakeBrain:
    def __init__(self, reply="respuesta", error=None):
        self.reply, self.error, self.calls = reply, error, []
        self.cancels = []

    def answer(self, actor, text, ctx, now, cancel=None):
        self.calls.append((actor.uid, actor.authorized, text))
        self.cancels.append(cancel)
        if self.error:
            raise self.error
        return self.reply


def _pos(uid, callsign, lat=5.1605, lon=-75.4930, now=NOW):
    return {"uid": uid, "type": "a-f-G-U-C", "callsign": callsign, "lat": lat, "lon": lon,
            "time": now, "stale": now + timedelta(minutes=5), "chat": None, "dest_uids": []}


def _chat(uid, callsign, text, room_id="mando-bot", now=NOW):
    return {"uid": f"GeoChat.{uid}.x", "type": "b-t-f", "callsign": None, "lat": 0.0, "lon": 0.0,
            "time": now, "stale": now + timedelta(hours=1), "dest_uids": [],
            "chat": {"room_name": "Mando", "room_id": room_id, "sender_uid": uid,
                     "sender_callsign": callsign, "text": text, "message_id": "m"}}


def _bot(tmp_path, brain=None, executor=None, zones=()):
    layer = Layer(tmp_path / "juego.geojson", tmp_path / "state.json")
    bot = Bot(lat=5.16, lon=-75.49, zones=zones, grid=GRID, announce=False, layer=layer,
              brain=brain, executor=executor or SyncExecutor(), admin_uids=("u-admin",),
              status_path=tmp_path / "status.json")
    bot.handle_event(_pos("u-admin", "santi"), NOW)
    bot.handle_event(_pos("u-guest", "Recon"), NOW)
    return bot, layer


def _texts(events):
    return [cot.parse_event(e)["chat"]["text"] for e in events if cot.parse_event(e).get("chat")]


def test_dm_goes_to_brain_and_reply_arrives_on_tick(tmp_path):
    brain = FakeBrain()
    bot, _ = _bot(tmp_path, brain)
    assert bot.handle_event(_chat("u-guest", "Recon", "qué hay en E5"), NOW) == []
    assert "respuesta" in _texts(bot.tick(NOW))
    assert brain.calls == [("u-guest", False, "qué hay en E5")]


def test_admin_is_authorized_in_brain(tmp_path):
    brain = FakeBrain()
    bot, _ = _bot(tmp_path, brain)
    bot.handle_event(_chat("u-admin", "santi", "marca X en E5"), NOW)
    assert brain.calls[0][1] is True


def test_all_chat_needs_name_prefix(tmp_path):
    brain = FakeBrain()
    bot, _ = _bot(tmp_path, brain)
    bot.handle_event(_chat("u-guest", "Recon", "hola a todos", room_id="All Chat Rooms"), NOW)
    bot.handle_event(_chat("u-guest", "Recon", "Mando, sitrep", room_id="All Chat Rooms", now=NOW + timedelta(seconds=5)), NOW + timedelta(seconds=5))
    assert brain.calls == [("u-guest", False, "sitrep")]


def test_bang_commands_skip_brain(tmp_path):
    brain = FakeBrain()
    bot, _ = _bot(tmp_path, brain)
    out = bot.handle_event(_chat("u-guest", "Recon", "!cuadro"), NOW)
    assert out and brain.calls == []


def test_brain_failure_message(tmp_path):
    bot, _ = _bot(tmp_path, FakeBrain(error=LlmError("down")))
    bot.handle_event(_chat("u-guest", "Recon", "hola"), NOW)
    assert "Sin cerebro ahora, usa !ayuda." in _texts(bot.tick(NOW))


def test_no_brain_configured_stays_silent(tmp_path):
    bot, _ = _bot(tmp_path, brain=None)
    assert bot.handle_event(_chat("u-guest", "Recon", "hola"), NOW) == []


def test_second_message_while_thinking(tmp_path):
    brain, ex = FakeBrain(), ManualExecutor()
    bot, _ = _bot(tmp_path, brain, ex)
    bot.handle_event(_chat("u-guest", "Recon", "uno"), NOW)
    later = NOW + timedelta(seconds=10)
    assert _texts(bot.handle_event(_chat("u-guest", "Recon", "dos", now=later), later)) == ["Sigo con tu mensaje anterior."]
    ex.finish()
    assert _texts(bot.tick(later)).count("respuesta") == 1
    assert [c[2] for c in brain.calls] == ["uno"]


def test_rate_limit_gap(tmp_path):
    bot, _ = _bot(tmp_path, FakeBrain())
    bot.handle_event(_chat("u-guest", "Recon", "uno"), NOW)
    bot.tick(NOW)
    soon = NOW + timedelta(seconds=2)
    assert _texts(bot.handle_event(_chat("u-guest", "Recon", "dos", now=soon), soon)) == ["Dame un respiro, prueba en un minuto."]


def test_ok_confirms_only_for_authorized(tmp_path):
    bot, layer = _bot(tmp_path, FakeBrain())
    layer.add_proposal("anunciar", {"texto": "x"}, "Recon", "u-guest", NOW, "anunciar x")
    assert _texts(bot.handle_event(_chat("u-guest", "Recon", "ok 1"), NOW)) == ["Solo un autorizado puede confirmar propuestas."]
    later = NOW + timedelta(seconds=10)
    assert _texts(bot.handle_event(_chat("u-admin", "santi", "ok 9", now=later), later)) == ["No existe la propuesta #9."]


def test_authorize_command(tmp_path):
    bot, layer = _bot(tmp_path, FakeBrain())
    assert _texts(bot.handle_event(_chat("u-guest", "Recon", "!autorizar santi"), NOW)) == ["Solo un autorizado puede autorizar."]
    assert _texts(bot.handle_event(_chat("u-admin", "santi", "!autorizar recon"), NOW)) == ["Recon autorizado."]
    assert "u-guest" in layer.authorized()
    later = NOW + timedelta(seconds=10)
    assert _texts(bot.handle_event(_chat("u-admin", "santi", "!autorizar nadie", now=later), later)) == ["No veo a 'nadie' conectado."]


def test_due_announcements_and_proposal_notices(tmp_path):
    bot, layer = _bot(tmp_path, FakeBrain())
    layer.add_announcement(NOW, "ALFA cierra", "todos")
    layer.add_proposal("borrar", {"objeto": "j-1"}, "Recon", "u-guest", NOW, "borrar j-1")
    texts = _texts(bot.tick(NOW))
    assert "ALFA cierra" in texts
    assert "Recon propone: borrar j-1. #1 → responde ok 1 o no 1" in texts
    assert "Recon propone" not in " ".join(_texts(bot.tick(NOW + timedelta(seconds=1))))


def test_layer_hazard_triggers_geofence_but_proposal_does_not(tmp_path):
    bot, layer = _bot(tmp_path, FakeBrain())
    ring = circle(5.1620, -75.4900, 30)
    layer.add_feature({"name": "Pozo", "folder": "Propuestas", "kind": "peligro"}, {"type": "Polygon", "coordinates": [ring]}, NOW, "u")
    bot.tick(NOW)
    assert bot.handle_event(_pos("u-guest", "Recon", 5.1620, -75.4900), NOW) == []
    layer.add_feature({"name": "Pozo2", "folder": "Juego", "kind": "peligro", "description": "Hondo."}, {"type": "Polygon", "coordinates": [ring]}, NOW, "u")
    bot.tick(NOW + timedelta(seconds=1))
    out = bot.handle_event(_pos("u-guest", "Recon", 5.16201, -75.49001, NOW + timedelta(seconds=2)), NOW + timedelta(seconds=2))
    assert _texts(out) == ["⚠ PELIGRO: Pozo2. Hondo."]


def test_corrupt_layer_keeps_previous_hazards(tmp_path):
    bot, layer = _bot(tmp_path, FakeBrain())
    ring = circle(5.1620, -75.4900, 30)
    layer.add_feature({"name": "Pozo", "folder": "Juego", "kind": "peligro"}, {"type": "Polygon", "coordinates": [ring]}, NOW, "u")
    bot.tick(NOW)
    (tmp_path / "juego.geojson").write_text("{roto", encoding="utf-8")
    bot.tick(NOW + timedelta(seconds=1))
    out = bot.handle_event(_pos("u-guest", "Recon", 5.1620, -75.4900, NOW + timedelta(seconds=2)), NOW + timedelta(seconds=2))
    assert _texts(out) == ["⚠ PELIGRO: Pozo."]


def test_status_snapshot(tmp_path):
    bot, _ = _bot(tmp_path, FakeBrain())
    bot.tick(NOW)
    data = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert {p["callsign"] for p in data["players"]} == {"santi", "Recon"}
    assert any(e["text"] == "Recon se conectó" for e in data["events"])


@pytest.mark.parametrize("broken", ['{"history": null}', "{roto"])
def test_damaged_state_never_crashes(tmp_path, broken):
    brain = FakeBrain()
    bot, _ = _bot(tmp_path, brain)
    (tmp_path / "state.json").write_text(broken, encoding="utf-8")
    later = NOW + timedelta(seconds=20)
    bot.tick(later)
    assert _texts(bot.handle_event(_chat("u-guest", "Recon", "hola", now=later), later)) == [
        "La capa de juego está dañada; avisa a un organizador."]
    assert _texts(bot.handle_event(_chat("u-guest", "Recon", "ok 1", now=later), later)) == [
        "La capa de juego está dañada; avisa a un organizador."]
    assert _texts(bot.handle_event(_chat("u-admin", "santi", "!autorizar recon", now=later), later)) == [
        "La capa de juego está dañada; avisa a un organizador."]
    out = bot.handle_event(_chat("u-guest", "Recon", "!cuadro", now=later), later)
    assert out != [] and _texts(out) != []


def test_ok_case_insensitive(tmp_path):
    bot, layer = _bot(tmp_path, FakeBrain())
    layer.add_proposal("anunciar", {"texto": "x"}, "Recon", "u-guest", NOW, "anunciar x")
    assert "aceptada" in _texts(bot.handle_event(_chat("u-admin", "santi", "OK 1"), NOW))[0]
    layer.add_proposal("anunciar", {"texto": "y"}, "Recon", "u-guest", NOW, "anunciar y")
    later = NOW + timedelta(seconds=5)
    assert "aceptada" in _texts(bot.handle_event(_chat("u-admin", "santi", "Ok 2", now=later), later))[0]
    layer.add_proposal("anunciar", {"texto": "z"}, "Recon", "u-guest", NOW, "anunciar z")
    later2 = NOW + timedelta(seconds=10)
    assert "descartada" in _texts(bot.handle_event(_chat("u-admin", "santi", "NO 3", now=later2), later2))[0]


def test_abandoned_worker_gets_cancel(tmp_path):
    brain, ex = FakeBrain(), ManualExecutor()
    bot, _ = _bot(tmp_path, brain, ex)
    bot.handle_event(_chat("u-guest", "Recon", "hola"), NOW)
    assert len(ex.jobs) == 1
    late = NOW + timedelta(seconds=46)
    assert "Sin cerebro ahora, usa !ayuda." in _texts(bot.tick(late))
    ex.finish()
    assert len(brain.cancels) == 1
    assert brain.cancels[0] is not None and brain.cancels[0].is_set()


def test_expired_contacts_are_removed_on_tick(tmp_path):
    bot, layer = _bot(tmp_path, FakeBrain())
    layer.add_feature({"name": "3 infantería", "folder": "Juego", "kind": "contacto", "expires": "2026-10-10T21:55:00Z"},
                      {"type": "Point", "coordinates": [-75.49, 5.16]}, NOW, "u")
    bot.tick(NOW)
    assert layer.features("Juego") == []


def test_bare_mando_prefix_does_nothing(tmp_path):
    brain = FakeBrain()
    bot, _ = _bot(tmp_path, brain)
    for text in ("Mando,", "Mando:", "Mando "):
        out = bot.handle_event(_chat("u-guest", "Recon", text, room_id="All Chat Rooms"), NOW)
        assert out == []
    assert brain.calls == []
