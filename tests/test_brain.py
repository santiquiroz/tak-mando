import io
import json
from datetime import datetime, timezone

import pytest

from mando.brain import Brain, LlmClient, LlmError, build_system_prompt, gateway_url
from mando.grid import Grid
from mando.tools import Actor
from mando.zones import Place, Zone

NOW = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)
ADMIN = Actor("u1", "santi", authorized=True)


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, tools):
        self.calls.append([dict(m) for m in messages])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def call(name, args, cid="c1"):
    arguments = args if isinstance(args, str) else json.dumps(args)
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": arguments}}]}


def say(text):
    return {"role": "assistant", "content": text}


def _brain(replies, executed=None, clock=None):
    executed = executed if executed is not None else []

    def execute(name, args, actor, ctx):
        executed.append((name, args, actor.uid))
        return f"ok {name}"

    return Brain(FakeClient(replies), execute, "SYS", clock=clock or Clock()), executed


def test_plain_answer():
    brain, executed = _brain([say("Hola")])
    assert brain.answer(ADMIN, "hola", None, NOW) == "Hola"
    assert executed == []


def test_one_tool_round():
    brain, executed = _brain([call("marcar_punto", {"nombre": "X"}), say("Listo, marcado.")])
    assert brain.answer(ADMIN, "marca X", None, NOW) == "Listo, marcado."
    assert executed == [("marcar_punto", {"nombre": "X"}, "u1")]
    second_call = brain.client.calls[1]
    assert second_call[-1] == {"role": "tool", "tool_call_id": "c1", "content": "ok marcar_punto"}


def test_invalid_arguments_not_executed():
    brain, executed = _brain([call("borrar", "{no json"), say("Perdón.")])
    assert brain.answer(ADMIN, "borra", None, NOW) == "Perdón."
    assert executed == []
    assert brain.client.calls[1][-1]["content"] == "Argumentos inválidos."


def test_rounds_exhausted():
    brain, _ = _brain([call("sitrep", {}, cid=str(i)) for i in range(4)])
    assert brain.answer(ADMIN, "?", None, NOW) == "No pude terminar, dime en una frase qué necesitas."


def test_llm_error_propagates():
    brain, _ = _brain([LlmError("caído")])
    with pytest.raises(LlmError):
        brain.answer(ADMIN, "hola", None, NOW)


def test_total_timeout():
    clock = Clock()
    brain, _ = _brain([call("sitrep", {}), say("tarde")], clock=clock)
    original = brain.client.chat

    def slow(messages, tools):
        clock.t += 50
        return original(messages, tools)

    brain.client.chat = slow
    assert brain.answer(ADMIN, "?", None, NOW) == "Se me acabó el tiempo pensando, prueba más corto."


def test_memory_kept_then_expires():
    clock = Clock()
    brain, _ = _brain([say("uno"), say("dos"), say("tres")], clock=clock)
    brain.answer(ADMIN, "a", None, NOW)
    brain.answer(ADMIN, "b", None, NOW)
    assert [m["content"] for m in brain.client.calls[1][1:]] == ["santi: a", "uno", "santi: b"]
    clock.t += 1000
    brain.answer(ADMIN, "c", None, NOW)
    assert [m["content"] for m in brain.client.calls[2][1:]] == ["santi: c"]


def test_answer_truncated():
    brain, _ = _brain([say("x" * 900)])
    out = brain.answer(ADMIN, "?", None, NOW)
    assert len(out) == 700 and out.endswith("…")


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_client_posts_and_parses():
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data)
        return FakeResponse(json.dumps({"choices": [{"message": {"role": "assistant", "content": "hi"}}]}).encode())

    client = LlmClient("http://172.18.48.1:8000/v1/", "k3y", "m", opener=opener)
    assert client.chat([{"role": "user", "content": "x"}], [])["content"] == "hi"
    assert seen["url"] == "http://172.18.48.1:8000/v1/chat/completions"
    assert seen["auth"] == "Bearer k3y"
    assert seen["body"]["model"] == "m" and seen["body"]["tool_choice"] == "auto"


def test_client_errors_hide_key():
    def opener(request, timeout):
        raise OSError("connection refused")

    with pytest.raises(LlmError) as exc:
        LlmClient("http://x/v1", "s3cret", "m", opener=opener).chat([], [])
    assert "s3cret" not in str(exc.value)

    def bad_json(request, timeout):
        return FakeResponse(b"<html>")

    with pytest.raises(LlmError):
        LlmClient("http://x/v1", "k", "m", opener=bad_json).chat([], [])


def test_gateway_url():
    route = (
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t013012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t003012AC\t00000000\t0001\t0\t0\t0\t00F0FFFF\t0\t0\t0\n"
    )
    assert gateway_url(route) == "http://172.18.48.1:8000/v1"
    assert gateway_url("Iface\tDestination\tGateway\n") is None


def test_system_prompt_has_grid_buildings_hazards():
    grid = Grid(north=5.1650, west=-75.4960, cell_m=100, cols=9, rows=9)
    places = [Place("12 Torre sur", 5.1594, -75.4934, None), Place("Llegada", 5.1580, -75.4920, None)]
    zones = [Zone("1 Tanque grande", [], "")]
    prompt = build_system_prompt("OP MEDUSA", grid, places, zones)
    assert "OP MEDUSA" in prompt
    assert "columnas A-I" in prompt and "filas 1-9" in prompt
    assert "12 Torre sur (" in prompt and "Llegada" not in prompt
    assert "1 Tanque grande" in prompt
    assert len(prompt) < 4000
