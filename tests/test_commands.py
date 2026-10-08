from datetime import datetime, timedelta, timezone

from mando.commands import Context, parse_command, run_command
from mando.geo import describe_offset
from mando.roster import Player
from mando.weather import Hour
from mando.zones import Place, Zone

NOW = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)


def _player(uid, callsign, lat=0.0, lon=0.0, seen=None):
    return Player(
        uid=uid, callsign=callsign, lat=lat, lon=lon,
        last_seen=seen or NOW, stale=None,
    )


def _ctx(**kw):
    base = dict(
        now_utc=NOW, utc_offset_h=-5, requester=None, players=[],
        places=[], zones=[], sun={}, moon=0.02, hours=[], forecast_age_s=None,
    )
    base.update(kw)
    return Context(**base)


def test_parse_aliases():
    assert parse_command("!ayuda")[0] == "ayuda"
    assert parse_command("!help")[0] == "ayuda"
    assert parse_command("!h")[0] == "ayuda"
    assert parse_command("!sol")[0] == "luz"
    assert parse_command("!luz")[0] == "luz"
    assert parse_command("!tiempo")[0] == "clima"
    assert parse_command("!team")[0] == "equipo"
    assert parse_command("!ubicar x")[0] == "donde"
    assert parse_command("!peligro")[0] == "peligros"
    assert parse_command("!mapas")[0] == "mapas"
    assert parse_command("!mapa")[0] == "mapas"
    assert parse_command("!paquete")[0] == "mapas"


def test_parse_accents_unknown_and_none():
    assert parse_command("!d\u00f3nde Recon") == ("donde", "Recon")
    assert parse_command("!foobar")[0] == "desconocido"
    assert parse_command("hola") is None
    assert parse_command("  hola !luz") is None


def test_parse_args_trimmed_and_capped():
    assert parse_command("!donde   Recon  ") == ("donde", "Recon")
    long_args = "x" * 50
    name, args = parse_command(f"!donde {long_args}")
    assert name == "donde"
    assert args == "x" * 40


def _sun():
    day = datetime(2026, 10, 10)
    return {
        "sunrise": day.replace(hour=5, minute=48),
        "sunset": day.replace(hour=17, minute=49),
        "nautical_dusk": day.replace(hour=18, minute=36),
    }


def test_luz_before_sunset():
    now_utc = datetime(2026, 10, 10, 21, 0, tzinfo=timezone.utc)
    ctx = _ctx(now_utc=now_utc, sun=_sun(), moon=0.02)
    assert run_command("luz", "", ctx) == (
        "Sol se pone 17:49 (en 1 h 49 min). Oscuridad total 18:36. Luna 2 %."
    )


def test_luz_twilight():
    now_utc = datetime(2026, 10, 10, 23, 0, tzinfo=timezone.utc)
    ctx = _ctx(now_utc=now_utc, sun=_sun(), moon=0.5)
    assert run_command("luz", "", ctx) == "Crep\u00fasculo. Oscuridad total 18:36 (en 36 min)."


def test_luz_night():
    now_utc = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)
    ctx = _ctx(now_utc=now_utc, sun=_sun(), moon=0.02)
    assert run_command("luz", "", ctx) == "Es de noche. Amanece 05:48 (en 1 h 48 min). Luna 2 %."


def _hour(hh, temp=20.0, prob=10, mm=0.0, day=10):
    return Hour(
        time=datetime(2026, 10, day, hh, 0, tzinfo=timezone.utc),
        temp_c=temp, rain_prob=prob, rain_mm=mm,
        cloud=10, visibility_m=10000.0, gust_kmh=5.0,
    )


def test_clima_with_data():
    now = datetime(2026, 10, 10, 22, 15, tzinfo=timezone.utc)
    hours = [_hour(22, 21.4, 10, 0.0), _hour(23, 20.1, 80, 2.5), _hour(0, 19.0, 50, 0.3, day=11)]
    ctx = _ctx(now_utc=now, hours=hours, forecast_age_s=300)
    assert run_command("clima", "", ctx) == (
        "17:00 21\u00b0C lluvia 10% 0.0 mm \u00b7 18:00 20\u00b0C lluvia 80% 2.5 mm \u00b7 "
        "19:00 19\u00b0C lluvia 50% 0.3 mm (pron\u00f3stico de hace 5 min)"
    )


def test_clima_empty():
    ctx = _ctx(hours=[])
    assert run_command("clima", "", ctx) == "Sin pron\u00f3stico: el servidor no pudo consultarlo."


def test_equipo_with_position():
    req = _player("r", "Base", lat=0.0, lon=0.0, seen=NOW)
    other = _player("a", "Recon", lat=0.001, lon=0.0, seen=NOW - timedelta(seconds=12))
    ctx = _ctx(requester=req, players=[req, other])
    offset = describe_offset(0.0, 0.0, 0.001, 0.0)
    assert run_command("equipo", "", ctx) == f"Recon {offset} (hace 12 s)"


def test_equipo_without_position_and_empty():
    a = _player("a", "Recon", seen=NOW - timedelta(minutes=3))
    b = _player("b", "Thomas", seen=NOW - timedelta(hours=1, minutes=5))
    ctx = _ctx(requester=None, players=[a, b])
    assert run_command("equipo", "", ctx) == "Recon (hace 3 min)\nThomas (hace 1 h)"
    assert run_command("equipo", "", _ctx()) == "No hay nadie m\u00e1s reportando posici\u00f3n."


def test_donde_usage_and_not_found():
    ctx = _ctx(players=[_player("a", "Recon")])
    assert run_command("donde", "", ctx) == "Uso: !donde <callsign>"
    assert run_command("donde", "Mike", ctx) == "No encuentro a 'Mike'. Conectados: Recon"


def test_donde_found():
    req = _player("r", "Base", lat=0.0, lon=0.0, seen=NOW)
    target = _player("a", "Recon", lat=0.001, lon=0.0, seen=NOW - timedelta(minutes=3))
    place = Place(name="Silo 2", lat=0.001, lon=0.0, ring=None)
    ctx = _ctx(requester=req, players=[req, target], places=[place])
    offset = describe_offset(0.0, 0.0, 0.001, 0.0)
    reply = run_command("donde", "recon", ctx)
    assert reply.startswith(f"Recon: {offset}")
    assert "(hace 3 min)" in reply
    ctx2 = _ctx(requester=None, players=[target], places=[])
    assert run_command("donde", "Recon", ctx2) == "Recon (hace 3 min)"


def test_peligros():
    ring = [[0.0, 0.0], [0.001, 0.0], [0.001, 0.001], [0.0, 0.001]]
    zone = Zone(name="Tanque", ring=ring, message="No entrar")
    inside = _player("r", "Base", lat=0.0005, lon=0.0005, seen=NOW)
    ctx = _ctx(requester=inside, zones=[zone])
    assert run_command("peligros", "", ctx) == "Tanque: EST\u00c1S DENTRO"
    nearby = _player("r", "Base", lat=0.002, lon=0.0005, seen=NOW)
    ctx2 = _ctx(requester=nearby, zones=[zone])
    reply = run_command("peligros", "", ctx2)
    assert reply.startswith("Tanque a ")
    far = _player("r", "Base", lat=0.05, lon=0.05, seen=NOW)
    assert run_command("peligros", "", _ctx(requester=far, zones=[zone])) == (
        "Ning\u00fan peligro marcado a menos de 200 m."
    )
    assert run_command("peligros", "", _ctx(requester=None, zones=[zone])) == (
        "No tengo tu posici\u00f3n todav\u00eda."
    )


def test_ayuda_and_desconocido():
    assert run_command("ayuda", "", _ctx()) == (
        "Comandos: !luz (sol y oscuridad) \u00b7 !clima (pr\u00f3ximas 3 h) \u00b7 "
        "!equipo (d\u00f3nde est\u00e1 cada uno) \u00b7 !donde <callsign> \u00b7 !peligros (cerca de ti) "
        "\u00b7 !mapas (paquete de mapas)"
    )
    assert run_command("desconocido", "", _ctx()) == "No conozco ese comando. Escribe !ayuda."


def test_reply_truncated():
    players = [_player(f"u{i}", f"P{i:02d}-" + "x" * 60, seen=NOW) for i in range(10)]
    ctx = _ctx(requester=None, players=players)
    reply = run_command("equipo", "", ctx)
    assert len(reply) == 700
    assert reply.endswith("\u2026")
