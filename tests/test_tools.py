import json
from datetime import datetime, timezone

import pytest

from test_elevation import _write_dted

from mando.commands import Context
from mando.elevation import line_of_sight, load_dted
from mando.events import EventLog
from mando.exposure import cell_of, load_exposure
from mando.geo import centroid, format_distance, haversine_m
from mando.grid import Grid
from mando.places import cell_center
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
    assert len(names) == len(set(names)) == 19
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


def test_contact_report_any_player_expires_and_announces(ctx):
    out = execute("reportar_contacto", {"tipo": "infanteria", "cantidad": 3, "lugar": "E6"}, GUEST, ctx)
    assert out.startswith("Contacto publicado: 3 infantería en E6. Se borra a las 17:10")
    f = ctx.layer.features("Juego")[0]["properties"]
    assert f["cot_type"] == "a-h-G-U-C-I" and f["stale_minutes"] == 10 and f["kind"] == "contacto"
    assert [a["text"] for a in ctx.layer.due_announcements(NOW)] == ["CONTACTO: 3 infantería en E6 (Recon, 17:00)."]
    assert execute("reportar_contacto", {"tipo": "dron", "lugar": "E6"}, GUEST, ctx) == "Espera unos segundos antes de otro reporte."


def test_enemy_point_gets_symbol(ctx):
    execute("marcar_punto", {"nombre": "Tirador", "lugar": "E6", "tipo": "enemigo"}, ADMIN, ctx)
    assert ctx.layer.get("j-1")["properties"]["cot_type"] == "a-h-G-U-C-I"


def test_ally_and_unknown_points_store_symbol(ctx):
    execute("marcar_punto", {"nombre": "Amigo", "lugar": "E5", "tipo": "aliado"}, ADMIN, ctx)
    execute("marcar_punto", {"nombre": "Sombra", "lugar": "E6", "tipo": "desconocido"}, ADMIN, ctx)
    assert ctx.layer.get("j-1")["properties"]["cot_type"] == "a-f-G-U-C-I"
    assert ctx.layer.get("j-2")["properties"]["cot_type"] == "a-u-G"


def test_contact_rate_limit_ignores_bad_and_future_created(ctx):
    execute("reportar_contacto", {"tipo": "infanteria", "lugar": "E6"}, GUEST, ctx)
    ctx.layer.update_feature("j-1", {"created": "basura"}, NOW, "u-guest")
    out = execute("reportar_contacto", {"tipo": "dron", "lugar": "E6"}, GUEST, ctx)
    assert out.startswith("Contacto publicado")
    ctx.layer.update_feature("j-2", {"created": "2026-10-10T23:00:00Z"}, NOW, "u-guest")
    out = execute("reportar_contacto", {"tipo": "dron", "lugar": "E5"}, GUEST, ctx)
    assert out.startswith("Contacto publicado")


def test_line_of_sight_without_dem(ctx):
    assert execute("linea_de_vista", {"desde": "12", "hasta": "E5"}, ADMIN, ctx) == "No tengo datos de elevación cargados."


def test_covered_route_without_exposure(ctx):
    assert execute("ruta_cubierta", {"desde": "E5", "hasta": "12"}, ADMIN, ctx) == "No tengo el mapa de visibilidad cargado."


def test_line_of_sight_visible_with_synthetic_dted(ctx, tmp_path):
    p = tmp_path / "flat.dt2"
    _write_dted(p, lambda c, r: 2000)
    ctx.dem = load_dted(p)
    vis, blocked, dist = line_of_sight(ctx.dem, 5.155, -75.4940, 1.7, 5.155, -75.4920, 1.7)
    assert vis is True and blocked is None
    expected = f"Desde 5.15500,-75.49400 a 5.15500,-75.49200 ({format_distance(dist)}): visible."
    out = execute("linea_de_vista", {"desde": "5.155,-75.4940", "hasta": "5.155,-75.4920"}, ADMIN, ctx)
    assert out == expected


def test_line_of_sight_blocked_by_ridge(ctx, tmp_path):
    p = tmp_path / "ridge.dt2"
    _write_dted(p, lambda c, r: 2100 if c == 25 else 2000)
    ctx.dem = load_dted(p)
    vis, blocked, dist = line_of_sight(ctx.dem, 5.155, -75.4940, 1.7, 5.155, -75.4920, 1.7)
    assert vis is False and 50 < blocked < 150
    expected = (f"Desde 5.15500,-75.49400 a 5.15500,-75.49200 ({format_distance(dist)}): "
                f"no visible, lo tapa el terreno a {format_distance(blocked)} de 5.15500,-75.49400.")
    out = execute("linea_de_vista", {"desde": "5.155,-75.4940", "hasta": "5.155,-75.4920"}, ADMIN, ctx)
    assert out == expected


def test_line_of_sight_uses_building_height(ctx, tmp_path):
    p = tmp_path / "ridge.dt2"
    _write_dted(p, lambda c, r: 2100 if c == 25 else 2000)
    ctx.dem = load_dted(p)
    args = {"desde": "5.155,-75.4940", "hasta": "5.155,-75.4920"}
    assert "no visible" in execute("linea_de_vista", args, ADMIN, ctx)
    ctx.heights = {"5.15500,-75.49400": 300.0}
    assert execute("linea_de_vista", args, ADMIN, ctx).endswith(": visible.")


def test_covered_route_creates_linestring(ctx, tmp_path):
    doc = {"north": 5.1650, "west": -75.4960, "cell_m": 50, "rows": 18, "cols": 18,
           "count": [[0] * 18 for _ in range(18)]}
    p = tmp_path / "exp.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    ctx.exposure = load_exposure(p)
    out = execute("ruta_cubierta", {"desde": "E5", "hasta": "E7"}, ADMIN, ctx)
    f = ctx.layer.get("j-1")
    assert f["geometry"]["type"] == "LineString"
    coords = f["geometry"]["coordinates"]
    assert len(coords) >= 2
    a_lat, a_lon = cell_center(GRID, "E5")
    b_lat, b_lon = cell_center(GRID, "E7")
    assert coords[0] == [a_lon, a_lat] and coords[-1] == [b_lon, b_lat]
    total = sum(haversine_m(coords[i - 1][1], coords[i - 1][0], coords[i][1], coords[i][0])
                for i in range(1, len(coords)))
    assert out == f"Ruta cubierta E5 → E7 dibujada ({format_distance(total)}, 0 % expuesta). id j-1."
    props = f["properties"]
    assert props["kind"] == "ruta" and props["stroke"] == "#34c759" and props["stroke-width"] == 4
    assert props["name"] == "Ruta cubierta E5→E7" and props["labels"] is True


def test_guest_covered_route_proposal_replays(ctx, tmp_path):
    doc = {"north": 5.1650, "west": -75.4960, "cell_m": 50, "rows": 18, "cols": 18,
           "count": [[0] * 18 for _ in range(18)]}
    p = tmp_path / "exp.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    ctx.exposure = load_exposure(p)
    out = execute("ruta_cubierta", {"desde": "E5", "hasta": "E7"}, GUEST, ctx)
    assert out.startswith("Propuesta #1")
    assert ctx.layer.features("Juego") == []
    out = execute("confirmar_propuesta", {"numero": 1, "aceptar": True}, ADMIN, ctx)
    assert "aceptada" in out
    game = ctx.layer.features("Juego")
    assert len(game) == 1
    assert game[0]["geometry"]["type"] == "LineString"
    coords = game[0]["geometry"]["coordinates"]
    a_lat, a_lon = cell_center(GRID, "E5")
    b_lat, b_lon = cell_center(GRID, "E7")
    assert coords[0][0] == pytest.approx(a_lon, abs=1e-6)
    assert coords[0][1] == pytest.approx(a_lat, abs=1e-6)
    assert coords[-1][0] == pytest.approx(b_lon, abs=1e-6)
    assert coords[-1][1] == pytest.approx(b_lat, abs=1e-6)


def _guest_at(ctx, ref):
    lat, lon = cell_center(GRID, ref)
    guest = Player("u-guest", "Recon", lat, lon, NOW, None)
    cc = ctx.command_context
    cc.requester = guest
    cc.players = [ME, guest]
    return guest


def _confirmer_at(ctx, guest, guest_ref, boss_ref):
    glat, glon = cell_center(GRID, guest_ref)
    guest.lat, guest.lon = glat, glon
    blat, blon = cell_center(GRID, boss_ref)
    boss = Player("u-admin", "santi", blat, blon, NOW, None)
    cc = ctx.command_context
    cc.players = [boss, guest]
    cc.requester = boss


def test_ruta_proposal_pins_relative_desde(ctx, tmp_path):
    doc = {"north": 5.1650, "west": -75.4960, "cell_m": 50, "rows": 18, "cols": 18,
           "count": [[0] * 18 for _ in range(18)]}
    p = tmp_path / "exp.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    ctx.exposure = load_exposure(p)
    p1_lat, p1_lon = cell_center(GRID, "C3")
    p1_lat += 0.0002
    p1_lon += 0.0002
    guest = Player("u-guest", "Recon", p1_lat, p1_lon, NOW, None)
    cc = ctx.command_context
    cc.requester = guest
    cc.players = [ME, guest]
    out = execute("ruta_cubierta", {"desde": "aquí", "hasta": "E7"}, GUEST, ctx)
    assert out.startswith("Propuesta #1")
    preview_name = ctx.layer.features("Propuestas")[0]["properties"]["name"]
    args = ctx.layer.proposals()[0]["args"]
    assert "desde_etiqueta" in args and "hasta_etiqueta" in args
    assert "aquí" not in json.dumps(args, ensure_ascii=False)
    _confirmer_at(ctx, guest, "A1", "H8")
    execute("confirmar_propuesta", {"numero": 1, "aceptar": True}, ADMIN, ctx)
    game = ctx.layer.features("Juego")
    assert len(game) == 1
    lon, lat = game[0]["geometry"]["coordinates"][0]
    assert cell_of(ctx.exposure, lat, lon) == cell_of(ctx.exposure, p1_lat, p1_lon)
    assert game[0]["properties"]["name"] == preview_name


def test_marcar_proposal_pins_aqui(ctx):
    p1_lat, p1_lon = cell_center(GRID, "C3")
    guest = _guest_at(ctx, "C3")
    out = execute("marcar_punto", {"nombre": "RALLY", "lugar": "aquí", "tipo": "reunion"}, GUEST, ctx)
    assert out.startswith("Propuesta #1")
    _confirmer_at(ctx, guest, "A1", "H8")
    execute("confirmar_propuesta", {"numero": 1, "aceptar": True}, ADMIN, ctx)
    game = ctx.layer.features("Juego")
    assert len(game) == 1
    lon, lat = game[0]["geometry"]["coordinates"]
    assert lat == pytest.approx(p1_lat, abs=2e-6)
    assert lon == pytest.approx(p1_lon, abs=2e-6)
    assert game[0]["properties"]["name"] == "RALLY"


def test_zona_proposal_pins_aqui(ctx):
    p1_lat, p1_lon = cell_center(GRID, "C3")
    guest = _guest_at(ctx, "C3")
    out = execute("dibujar_zona", {"nombre": "Z", "lugar": "aquí", "radio_m": 50, "kind": "zona"}, GUEST, ctx)
    assert out.startswith("Propuesta #1")
    _confirmer_at(ctx, guest, "A1", "H8")
    execute("confirmar_propuesta", {"numero": 1, "aceptar": True}, ADMIN, ctx)
    game = ctx.layer.features("Juego")
    assert len(game) == 1
    lat, lon = centroid(game[0]["geometry"]["coordinates"][0])
    assert lat == pytest.approx(p1_lat, abs=1e-5)
    assert lon == pytest.approx(p1_lon, abs=1e-5)


def test_mover_proposal_pins_aqui(ctx):
    execute("marcar_punto", {"nombre": "CAJA", "lugar": "E5", "tipo": "info"}, ADMIN, ctx)
    p1_lat, p1_lon = cell_center(GRID, "C3")
    guest = _guest_at(ctx, "C3")
    out = execute("mover", {"objeto": "CAJA", "lugar": "aquí"}, GUEST, ctx)
    assert out.startswith("Propuesta #1")
    label = out.split(" a ", 1)[1].split(".")[0]
    _confirmer_at(ctx, guest, "A1", "H8")
    confirm = execute("confirmar_propuesta", {"numero": 1, "aceptar": True}, ADMIN, ctx)
    assert confirm == f"Propuesta #1 aceptada: Movido CAJA a {label}."
    lon, lat = ctx.layer.get("j-1")["geometry"]["coordinates"]
    assert haversine_m(lat, lon, p1_lat, p1_lon) < 1.0


def test_tool_schemas_hide_etiqueta_keys():
    assert "_etiqueta" not in json.dumps(TOOLS, ensure_ascii=False)
