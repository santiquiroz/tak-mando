import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from mando.grid import Grid
from mando.layer import Layer
from mando.mcp import McpServer, load_status
from mando.zones import Place

NOW = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)
GRID = Grid(north=5.1650, west=-75.4960, cell_m=100, cols=9, rows=9)


def _server(tmp_path, status=None):
    layer = Layer(tmp_path / "juego.geojson", tmp_path / "state.json")
    status_path = tmp_path / "status.json"
    if status is not None:
        status_path.write_text(json.dumps(status), encoding="utf-8")
    places = [Place("12 Torre sur", 5.1594, -75.4934, None)]
    return McpServer(layer, [], places, GRID, status_path, 5.16, -75.49, clock=lambda: NOW), layer


def _rpc(server, method, params=None, rid=1):
    return server.handle({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})


def test_initialize_and_list(tmp_path):
    server, _ = _server(tmp_path)
    init = _rpc(server, "initialize", {"protocolVersion": "2025-06-18"})
    assert init["result"]["capabilities"] == {"tools": {}}
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = [t["name"] for t in _rpc(server, "tools/list")["result"]["tools"]]
    assert "marcar_punto" in names and "inputSchema" in _rpc(server, "tools/list")["result"]["tools"][0]


def test_call_writes_layer_as_authorized(tmp_path):
    server, layer = _server(tmp_path)
    res = _rpc(server, "tools/call", {"name": "marcar_punto", "arguments": {"nombre": "EXFIL", "lugar": "E5", "tipo": "reunion"}})
    assert res["result"]["content"][0]["text"] == "Marcado EXFIL (reunion) en E5. id j-1."
    assert layer.get("j-1")["properties"]["folder"] == "Juego"


def test_stale_or_missing_status_is_flagged(tmp_path):
    server, _ = _server(tmp_path)
    text = _rpc(server, "tools/call", {"name": "estado_equipo", "arguments": {}})["result"]["content"][0]["text"]
    assert text.startswith("(Sin datos de jugadores: el bot no está corriendo.)")


def test_fresh_status_players(tmp_path):
    status = {"updated": "2026-10-10T21:59:55Z", "events": [],
              "players": [{"uid": "u1", "callsign": "Recon", "lat": 5.161, "lon": -75.492, "last_seen": "2026-10-10T21:59:50Z"}]}
    server, _ = _server(tmp_path, status)
    text = _rpc(server, "tools/call", {"name": "donde_esta", "arguments": {"callsign": "Recon"}})["result"]["content"][0]["text"]
    assert "Recon" in text and not text.startswith("(Sin datos")
    players, _events, age = load_status(tmp_path / "status.json", NOW)
    assert players[0].callsign == "Recon" and age == 5.0


def test_unknown_method_and_bad_json(tmp_path):
    server, _ = _server(tmp_path)
    assert _rpc(server, "nada")["error"]["code"] == -32601


def test_stdio_process(tmp_path):
    zones = tmp_path / "campo.geojson"
    zones.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "12 Torre sur"}, "geometry": {"type": "Point", "coordinates": [-75.4934, 5.1594]}}]}), encoding="utf-8")
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "mando.mcp", "--layer", str(tmp_path / "juego.geojson"),
         "--state", str(tmp_path / "state.json"), "--status", str(tmp_path / "status.json"),
         "--zones", str(zones), "--grid", "5.1650,-75.4960,100,9,9"],
        input="\n".join(json.dumps(l) for l in lines) + "\nno-json\n",
        capture_output=True, text=True, timeout=30, cwd=Path(__file__).resolve().parents[1],
    )
    out = [json.loads(l) for l in proc.stdout.splitlines()]
    assert [o.get("id") for o in out] == [1, 2, None]
    assert out[2]["error"]["code"] == -32700
