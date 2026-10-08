import json
from datetime import datetime, timezone

import pytest

from mando.commands import Context
from mando.events import EventLog
from mando.grid import Grid
from mando.layer import Layer
from mando.roster import Player
from mando.tools import TOOLS, Actor, ToolContext, clean, execute, proposal_notice
from mando.zones import Place, Zone

NOW = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)
GRID = Grid(north=5.1650, west=-75.4960, cell_m=100, cols=9, rows=9)
TANK = [[-75.4915, 5.1612], [-75.4908, 5.1612], [-75.4908, 5.1618], [-75.4915, 5.1618], [-75.4915, 5.1612]]
PLACES = [Place("12 Torre sur", 5.1594, -75.4934, None), Place("1 Tanque grande", 5.1615, -75.49115, TANK)]
ZONES = [Zone("1 Tanque grande", TANK, "No entrar.")]
ADMIN = Actor("u-admin", "santi", authorized=True)
GUEST = Actor("u-guest", "Recon", authorized=False)
ME = Player("u-admin", "santi", 5.1605, -75.4930, NOW, None)
RECON = Player("u-guest", "Recon", 5.1610, -75.4920, NOW, None)


@pytest.fixture
def ctx(tmp_path):
    cc = Context(
        now_utc=NOW, utc_offset_h=-5.0, requester=ME, players=[ME, RECON],
        places=PLACES, zones=ZONES, sun={}, moon=0.0, hours=[], forecast_age_s=None, grid=GRID,
    )
    layer = Layer(tmp_path / "juego.geojson", tmp_path / "state.json")
    return ToolContext(layer=layer, events=EventLog(), command_context=cc)


def test_tools_schema_names_unique_and_valid():
    names = [t["function"]["name"] for t in TOOLS]
    assert len(names) == len(set(names)) == 16
    for t in TOOLS:
        params = t["function"]["parameters"]
        assert params["type"] == "object"
        assert set(params["required"]) <= set(params["properties"])


def test_admin_marks_point(ctx):
    out = execute("marcar_punto", {"nombre": "EXFIL ALFA", "lugar": "E5", "tipo": "reunion"}, ADMIN, ctx)
    assert out == "Marcado EXFIL ALFA (reunion) en E5. id j-1."
    f = ctx.layer.get("j-1")
    assert f["properties"]["folder"] == "Juego"
    assert f["properties"]["marker-color"] == "#32d2ff"
    assert [e.kind for e in ctx.events.since(NOW, 1)] == ["mapa"]


def test_guest_write_becomes_proposal(ctx):
    out = execute("marcar_punto", {"nombre": "EXFIL", "lugar": "E5", "tipo": "reunion"}, GUEST, ctx)
    assert out.startswith("Propuesta #1: marcar EXFIL (reunion) en E5")
    p = ctx.layer.proposals()[0]
    assert p["author"] == "Recon" and p["fid"] == "j-1"
    assert ctx.layer.get("j-1")["properties"]["folder"] == "Propuestas"
    assert proposal_notice(p) == "Recon propone: marcar EXFIL (reunion) en E5. #1 → responde ok 1 o no 1"


def test_guest_cannot_announce_or_confirm(ctx):
    assert execute("anunciar", {"texto": "hola"}, GUEST, ctx) == "Solo un autorizado puede anunciar a todos."
    assert execute("confirmar_propuesta", {"numero": 1, "aceptar": True}, GUEST, ctx) == "Solo un autorizado puede confirmar propuestas."


def test_confirm_moves_proposal_to_game(ctx):
    execute("marcar_punto", {"nombre": "EXFIL", "lugar": "E5", "tipo": "reunion"}, GUEST, ctx)
    out = execute("confirmar_propuesta", {"numero": 1, "aceptar": True}, ADMIN, ctx)
    assert out.startswith("Propuesta #1 aceptada: Marcado EXFIL")
    game = ctx.layer.features("Juego")
    assert [f["properties"]["name"] for f in game] == ["EXFIL"]
    assert game[0]["properties"]["author"] == "Recon"
    assert ctx.layer.features("Propuestas") == []
    assert execute("confirmar_propuesta", {"numero": 1, "aceptar": True}, ADMIN, ctx) == "No existe la propuesta #1."


def test_reject_proposal(ctx):
    execute("dibujar_zona", {"nombre": "Z", "lugar": "E5", "radio_m": 50, "kind": "zona"}, GUEST, ctx)
    assert execute("confirmar_propuesta", {"numero": 1, "aceptar": False}, ADMIN, ctx) == "Propuesta #1 descartada."
    assert ctx.layer.features() == []


def test_zone_is_polygon_and_objective_status(ctx):
    execute("dibujar_zona", {"nombre": "ALFA", "lugar": "D6", "radio_m": 40, "kind": "objetivo"}, ADMIN, ctx)
    out = execute("estado_objetivo", {"objetivo": "alfa", "estado": "nuestro"}, ADMIN, ctx)
    assert out == "ALFA: nuestro."
    f = ctx.layer.get("j-1")
    assert f["geometry"]["type"] == "Polygon"
    assert f["properties"]["status"] == "nuestro" and f["properties"]["stroke"] == "#34c759"


def test_status_on_non_objective(ctx):
    execute("marcar_punto", {"nombre": "MED", "lugar": "E5", "tipo": "medico"}, ADMIN, ctx)
    assert execute("estado_objetivo", {"objetivo": "MED", "estado": "nuestro"}, ADMIN, ctx) == "MED no es un objetivo."


def test_ambiguous_reference_touches_nothing(ctx):
    execute("marcar_punto", {"nombre": "EXFIL Alfa", "lugar": "E5", "tipo": "info"}, ADMIN, ctx)
    execute("marcar_punto", {"nombre": "EXFIL Bravo", "lugar": "E6", "tipo": "info"}, ADMIN, ctx)
    out = execute("borrar", {"objeto": "exfil"}, ADMIN, ctx)
    assert out == "Hay varios que coinciden con 'exfil': j-1 EXFIL Alfa, j-2 EXFIL Bravo. Usa el id."
    assert len(ctx.layer.features()) == 2


def test_write_limit(ctx):
    ctx.writes_left = 1
    execute("marcar_punto", {"nombre": "A", "lugar": "E5", "tipo": "info"}, ADMIN, ctx)
    assert execute("marcar_punto", {"nombre": "B", "lugar": "E5", "tipo": "info"}, ADMIN, ctx) == "Límite de cambios por mensaje alcanzado."


def test_bad_arguments_never_raise(ctx):
    assert execute("marcar_punto", {"lugar": "E5", "tipo": "info"}, ADMIN, ctx) == "Faltan datos: nombre."
    assert execute("dibujar_zona", {"nombre": "Z", "lugar": "E5", "radio_m": 5000, "kind": "zona"}, ADMIN, ctx) == "radio_m debe estar entre 10 y 300."
    assert execute("marcar_punto", {"nombre": "A", "lugar": "E5", "tipo": "tanque"}, ADMIN, ctx) == "tipo no válido: tanque."
    assert execute("volar", {}, ADMIN, ctx) == "Herramienta desconocida: volar."
    assert execute("marcar_punto", {"nombre": "A", "lugar": "Z99", "tipo": "info"}, ADMIN, ctx).startswith("El cuadro Z99")


def test_hostile_text_is_cleaned(ctx):
    name = "<b>&x\n" + "A" * 500
    execute("marcar_punto", {"nombre": name, "lugar": "E5", "tipo": "info"}, ADMIN, ctx)
    stored = ctx.layer.get("j-1")["properties"]["name"]
    assert "\n" not in stored and len(stored) <= 40
    assert clean("a\x00b\u202ec  d", 10) == "abc d"


def test_corrupt_layer_message(ctx, tmp_path):
    (tmp_path / "juego.geojson").write_text("{roto", encoding="utf-8")
    out = execute("marcar_punto", {"nombre": "A", "lugar": "E5", "tipo": "info"}, ADMIN, ctx)
    assert out == "La capa de juego está dañada; avisa a un organizador."
    assert (tmp_path / "juego.geojson").read_text(encoding="utf-8") == "{roto"


def test_undo_and_move(ctx):
    execute("marcar_punto", {"nombre": "A", "lugar": "E5", "tipo": "info"}, ADMIN, ctx)
    assert execute("mover", {"objeto": "j-1", "lugar": "12"}, ADMIN, ctx) == "Movido A a 12 Torre sur."
    assert execute("deshacer", {}, ADMIN, ctx).startswith("Deshecho")


def test_announce_and_schedule_go_through_layer(ctx):
    assert execute("anunciar", {"texto": "ALFA cae en 5"}, ADMIN, ctx) == "Anuncio enviado."
    out = execute("programar_aviso", {"minutos": 20, "texto": "cierra", "para": "todos"}, ADMIN, ctx)
    assert out == "Aviso #2 programado para las 17:20."
    assert [a["text"] for a in ctx.layer.due_announcements(NOW)] == ["ALFA cae en 5"]


def test_what_is_here_lists_buildings_hazards_and_layer(ctx):
    execute("marcar_punto", {"nombre": "MED", "lugar": "1", "tipo": "medico"}, ADMIN, ctx)
    out = execute("que_hay_en", {"lugar": "1"}, ADMIN, ctx)
    assert "1 Tanque grande" in out and "Peligro: 1 Tanque grande" in out and "j-1 MED" in out


def test_what_is_here_does_not_repeat_places(ctx):
    ctx.command_context.places.append(Place("1 Tanque grande", 5.1615, -75.49115, None))
    out = execute("que_hay_en", {"lugar": "1"}, ADMIN, ctx)
    assert out.count("1 Tanque grande (") == 1


def test_sitrep_and_layer_listing(ctx):
    assert execute("sitrep", {}, ADMIN, ctx) == "Sin novedades en los últimos 10 min."
    execute("marcar_punto", {"nombre": "A", "lugar": "E5", "tipo": "info"}, ADMIN, ctx)
    assert "santi: marcar A (info) en E5" in execute("sitrep", {"minutos": 5}, ADMIN, ctx)
    assert execute("capa_juego", {}, ADMIN, ctx).startswith("j-1 A (info, E5)")


def test_hazards_include_layer_points(ctx):
    execute("marcar_punto", {"nombre": "Pozo", "lugar": "E5", "tipo": "peligro"}, ADMIN, ctx)
    assert "Pozo" in execute("peligros", {}, ADMIN, ctx)
