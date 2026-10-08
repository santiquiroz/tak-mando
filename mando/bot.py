"""Runtime: connection package, TLS, the Bot state machine and the run loop.

All CoT XML is built and parsed through mando.cot; this module never
assembles XML itself.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mando import __version__
from mando.commands import Context, parse_command, run_command
from mando.cot import (
    all_chat_event,
    dm_event,
    fileshare_event,
    identity_event,
    parse_event,
    split_stream,
)
from mando.grid import grid_ref
from mando.roster import Roster
from mando.rules import (
    Announcer,
    GeofenceTracker,
    LostContactTracker,
    RainWatch,
    light_schedule,
)
from mando.sun import moon_illumination, sun_events
from mando.weather import ForecastCache, fetch_forecast
from mando.zones import describe_location, load_places, load_zones

IDENTITY_EVERY_S = 60.0
LOST_EVERY_S = 30.0
ANNOUNCE_EVERY_S = 60.0
COMMAND_GAP_S = 3.0
CHAT_MAX_AGE_S = 120.0
DEFAULT_PASSWORD = "atakatak"

_BACKOFFS = (5, 10, 20, 40, 60)


@dataclass
class PackageInfo:
    host: str
    port: int
    password: str
    client_p12: bytes
    trust_p12: bytes


@dataclass
class SharedPackage:
    path: Path
    filename: str
    name: str
    sha256: str
    size_bytes: int
    url: str


def shared_package(path, name, url_base) -> SharedPackage:
    """Describe a field data package file for sharing by sha256 link."""
    where = Path(path)
    try:
        data = where.read_bytes()
    except OSError as exc:
        raise ValueError(
            f"no se encontró el paquete para compartir: {path}"
        ) from exc
    digest = hashlib.sha256(data).hexdigest()
    return SharedPackage(
        path=where,
        filename=where.name,
        name=name,
        sha256=digest,
        size_bytes=len(data),
        url=f"{url_base.rstrip('/')}/Marti/sync/content?hash={digest}",
    )


def _load_sent_state(path) -> dict:
    """Load {sha256: set(uids)}; a missing or invalid file means empty."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, list):
            out[key] = {u for u in value if isinstance(u, str)}
    return out


def _save_sent_state(path, state: dict) -> None:
    """Persist {sha256: set(uids)} atomically (temp file + os.replace)."""
    target = Path(path)
    payload = {key: sorted(uids) for key, uids in state.items()}
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError:
        pass


def _collect_package_files(zf: zipfile.ZipFile, depth: int, files: list) -> None:
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        try:
            data = zf.read(name)
        except KeyError:
            continue
        if name.lower().endswith(".zip") and depth < 2:
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as nested:
                    _collect_package_files(nested, depth + 1, files)
            except zipfile.BadZipFile:
                pass
        else:
            files.append((name, data))


def _pref_value(text: str, key: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(key):
            rest = stripped[len(key):].strip()
            if rest.startswith("="):
                return rest[1:].strip()
    xml = re.search(
        r'(?:key|name)="' + re.escape(key) + r'"[^>]*>([^<]*)<', text
    )
    if xml is not None:
        return xml.group(1).strip()
    return None


def read_package(path) -> PackageInfo:
    """Read an OpenTAKServer/ATAK connection package (possibly nested zips)."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(
            f"no se pudo leer el paquete de conexión: {path}"
        ) from exc
    with zf:
        files: list = []
        _collect_package_files(zf, 0, files)
    pref_text = None
    for name, data in files:
        if name.lower().endswith(".pref"):
            text = data.decode("utf-8", errors="replace")
            if "connectString0" in text:
                pref_text = text
                break
    if pref_text is None:
        raise ValueError(
            "no se encontró el archivo .pref con connectString0 en el paquete"
        )
    conn = _pref_value(pref_text, "connectString0")
    parts = (conn or "").split(":")
    host = parts[0].strip() if parts else ""
    try:
        port = int(parts[1].strip()) if len(parts) > 1 else 0
    except ValueError:
        port = 0
    if not host or port <= 0:
        raise ValueError(
            f"connectString0 con formato inválido en el paquete: {conn!r} "
            "(se esperaba host:puerto:ssl)"
        )
    password = _pref_value(pref_text, "clientPassword") or DEFAULT_PASSWORD
    client = None
    trust = None
    for name, data in files:
        if not name.lower().endswith(".p12"):
            continue
        if "truststore" in name.lower():
            if trust is None:
                trust = data
        elif client is None:
            client = data
    if client is None:
        raise ValueError(
            "no se encontró el certificado cliente .p12 en el paquete"
        )
    if trust is None:
        raise ValueError(
            "no se encontró el almacén de confianza truststore .p12 en el paquete"
        )
    return PackageInfo(
        host=host, port=port, password=password,
        client_p12=client, trust_p12=trust,
    )


def pem_from_p12(
    p12: bytes, password: str, out: Path, extra: list[str], openssl: str
) -> Path:
    """Convert a .p12 blob to PEM; the password travels by environment."""
    out = Path(out)
    p12_path = Path(str(out) + ".p12")
    p12_path.write_bytes(p12)
    try:
        subprocess.run(
            [openssl, "pkcs12", "-legacy", "-in", str(p12_path),
             "-out", str(out), "-passin", "env:MANDO_P12_PASSWORD", *extra],
            env={**os.environ, "MANDO_P12_PASSWORD": password},
            check=True,
            capture_output=True,
        )
    finally:
        try:
            p12_path.unlink()
        except OSError:
            pass
    return out


def tls_connect(info: PackageInfo, host: str, port: int, workdir, openssl: str):
    """Connect to the TAK server with mutual TLS from the package material."""
    workdir = Path(workdir)
    client_pem = pem_from_p12(
        info.client_p12, info.password, workdir / "client.pem", ["-nodes"],
        openssl,
    )
    trust_pem = pem_from_p12(
        info.trust_p12, info.password, workdir / "trust.pem", ["-nokeys"],
        openssl,
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # The server certificate does not carry the connect address, but the chain is still verified against the package CA.
    ctx.check_hostname = False
    ctx.load_verify_locations(cafile=str(trust_pem))
    ctx.load_cert_chain(certfile=str(client_pem))
    sock = socket.create_connection((host, port), timeout=15)
    try:
        tls = ctx.wrap_socket(sock, server_hostname=host)
    except Exception:
        sock.close()
        raise
    tls.settimeout(None)
    return tls


class Bot:
    """All bot state. Free of sockets so it stays unit testable."""

    def __init__(
        self,
        uid="mando-bot",
        callsign="Mando",
        lat=0.0,
        lon=0.0,
        utc_offset_h=-5.0,
        zones=(),
        places=(),
        roster=None,
        geofence=None,
        lost=None,
        announcer=None,
        rain=None,
        forecast_cache=None,
        lost_after=300,
        geofence_cooldown=180,
        rain_mm=2.0,
        announce=True,
        ignore_prefixes=("overlay-",),
        version=None,
        package=None,
        sent_state=None,
        grid=None,
    ):
        self.uid = uid
        self.callsign = callsign
        self.lat = lat
        self.lon = lon
        self.utc_offset_h = utc_offset_h
        self.zones = list(zones)
        self.places = list(places)
        self.roster = roster if roster is not None else Roster(
            ignore_prefixes=tuple(ignore_prefixes)
        )
        self.geofence = geofence if geofence is not None else GeofenceTracker(
            self.zones, cooldown_s=geofence_cooldown
        )
        self.lost = lost if lost is not None else LostContactTracker(
            after_s=lost_after
        )
        self.announcer = announcer if announcer is not None else Announcer()
        self.rain = rain if rain is not None else RainWatch(
            threshold_mm=rain_mm
        )
        self.forecast_cache = (
            forecast_cache if forecast_cache is not None
            else ForecastCache(lambda: [])
        )
        self.announce = announce
        self.version = version if version is not None else __version__
        self.package = package
        self.sent_state = sent_state
        self.grid = grid
        self._sent: dict = (
            _load_sent_state(sent_state) if sent_state is not None else {}
        )
        self.last_cmd: dict = {}
        self._sun_day = None
        self._sun: dict = {}
        self._last_identity = None
        self._last_lost = None
        self._last_announce = None

    def _local_now(self, now: datetime) -> datetime:
        return (now + timedelta(hours=self.utc_offset_h)).replace(tzinfo=None)

    def _sun_for(self, now: datetime) -> dict:
        local = self._local_now(now)
        if self._sun_day != local.date():
            self._sun_day = local.date()
            try:
                self._sun = sun_events(
                    local.date(), self.lat, self.lon, self.utc_offset_h
                )
            except Exception:
                self._sun = {}
        return self._sun

    def _forecast(self, now: datetime):
        try:
            hours = self.forecast_cache.get(now)
        except Exception:
            hours = []
        fetched = self.forecast_cache.fetched_at
        age = (now - fetched).total_seconds() if fetched is not None else None
        return (hours or [], age)

    def identity(self, now: datetime) -> str:
        self._last_identity = now
        return identity_event(
            self.uid, self.callsign, self.lat, self.lon, now,
            version=self.version,
        )

    def handle_event(self, event: dict, now: datetime) -> list[str]:
        """Route one parsed event; returns outgoing CoT strings to send."""
        out: list[str] = []
        sun = self._sun_for(now)
        player = None
        if isinstance(event, dict):
            try:
                player = self.roster.update(event, now)
            except Exception:
                player = None
            contact = self.roster.contact(event, now) if self.package is not None else None
            if contact is not None:
                out.extend(self._package_once(contact[0], contact[1], now))
            if player is not None:
                for msg in self.geofence.check(player, now):
                    out.append(dm_event(
                        self.uid, self.callsign, player.uid,
                        player.callsign, msg, now,
                    ))
            chat = event.get("chat")
            if chat:
                reply = self._command_reply(chat, event, now, sun)
                if isinstance(reply, list):
                    out.extend(reply)
                elif reply is not None:
                    out.append(reply)
        return out

    def _package_once(self, uid: str, callsign: str, now: datetime) -> list[str]:
        sent = self._sent.setdefault(self.package.sha256, set())
        if uid in sent:
            return []
        sent.add(uid)
        if self.sent_state is not None:
            _save_sent_state(self.sent_state, self._sent)
        return [
            fileshare_event(
                self.uid, self.callsign, uid, self.package.filename, self.package.name,
                self.package.url, self.package.size_bytes, self.package.sha256, now,
            ),
            dm_event(
                self.uid, self.callsign, uid, callsign,
                f'Te envié el paquete del campo "{self.package.name}" (mapas satelitales '
                "y capa táctica). Acéptalo en la notificación de ATAK/iTAK. Si no te "
                "llegó, escribe !mapas.",
                now,
            ),
        ]

    def _command_reply(
        self, chat: dict, event: dict, now: datetime, sun: dict
    ) -> str | list[str] | None:
        sender = chat.get("sender_uid") or ""
        if sender == "" or sender == self.uid:
            return None
        stamp = event.get("time")
        if stamp is not None:
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if (now - stamp).total_seconds() > CHAT_MAX_AGE_S:
                return None
        parsed = parse_command(chat.get("text") or "")
        if parsed is None:
            return None
        name, args = parsed
        last = self.last_cmd.get(sender)
        if last is not None and (now - last).total_seconds() < COMMAND_GAP_S:
            return None
        self.last_cmd[sender] = now
        requester = self.roster.get(sender)
        if name == "mapas" and self.package is not None:
            return self._mapas_reply(sender, requester, chat, now)
        hours, forecast_age = self._forecast(now)
        ctx = Context(
            now_utc=now,
            utc_offset_h=self.utc_offset_h,
            requester=requester,
            players=self.roster.players(),
            places=self.places,
            zones=self.zones,
            sun=sun,
            moon=moon_illumination(now),
            hours=hours,
            forecast_age_s=forecast_age,
            grid=self.grid,
        )
        text = run_command(name, args, ctx)
        if (chat.get("room_id") or "") == self.uid:
            to_callsign = (
                requester.callsign if requester is not None
                else (chat.get("sender_callsign") or sender)
            )
            return dm_event(
                self.uid, self.callsign, sender, to_callsign, text, now
            )
        return all_chat_event(self.uid, self.callsign, text, now)

    def _mapas_reply(
        self, sender: str, requester, chat: dict, now: datetime
    ) -> list[str]:
        share = fileshare_event(
            self.uid, self.callsign, sender,
            self.package.filename, self.package.name,
            self.package.url, self.package.size_bytes,
            self.package.sha256, now,
        )
        text = (
            f"Paquete enviado: {self.package.name}. "
            "Acéptalo en la notificación."
        )
        if (chat.get("room_id") or "") == self.uid:
            to_callsign = (
                requester.callsign if requester is not None
                else (chat.get("sender_callsign") or sender)
            )
            reply = dm_event(
                self.uid, self.callsign, sender, to_callsign, text, now
            )
        else:
            reply = all_chat_event(self.uid, self.callsign, text, now)
        return [share, reply]

    def tick(self, now: datetime) -> list[str]:
        """Periodic work: identity, lost contact, announcements."""
        out: list[str] = []
        sun = self._sun_for(now)
        if (
            self._last_identity is None
            or (now - self._last_identity).total_seconds() >= IDENTITY_EVERY_S
        ):
            out.append(self.identity(now))
        if (
            self._last_lost is None
            or (now - self._last_lost).total_seconds() >= LOST_EVERY_S
        ):
            self._last_lost = now
            for player in self.lost.check(self.roster.players(), now):
                mins = int((now - player.last_seen).total_seconds() // 60)
                ref = None
                if self.grid is not None:
                    ref = grid_ref(self.grid, player.lat, player.lon)
                loc = describe_location(
                    self.places, player.lat, player.lon
                )
                parts = []
                if ref is not None:
                    parts.append(ref)
                if loc:
                    parts.append(loc)
                if not parts:
                    parts.append(f"{player.lat:.5f},{player.lon:.5f}")
                where = ", ".join(parts)
                out.append(all_chat_event(
                    self.uid, self.callsign,
                    f"⚠ {player.callsign} lleva {mins} min sin reportar. "
                    f"Última posición: {where}.",
                    now,
                ))
        if self.announce and (
            self._last_announce is None
            or (now - self._last_announce).total_seconds()
            >= ANNOUNCE_EVERY_S
        ):
            self._last_announce = now
            moon = moon_illumination(now)
            for text in self.announcer.due(
                light_schedule(sun, moon), self._local_now(now)
            ):
                out.append(all_chat_event(self.uid, self.callsign, text, now))
            hours, _age = self._forecast(now)
            msg = self.rain.check(hours, now, self.utc_offset_h)
            if msg is not None:
                out.append(all_chat_event(self.uid, self.callsign, msg, now))
        return out


def _log(*parts) -> None:
    print(datetime.now().strftime("%H:%M:%S"), *parts, flush=True)


def _short(text: str, limit: int = 160) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _log_outgoing(out: str) -> None:
    parsed = parse_event(out)
    if parsed is None:
        _log("envío sin parsear")
        return
    if parsed.get("type") == "b-f-t-r":
        dests = ", ".join(parsed.get("dest_uids") or []) or "?"
        _log(f"paquete {dests}")
    elif parsed.get("type") == "b-t-f":
        chat = parsed.get("chat") or {}
        text = _short(chat.get("text") or "")
        if parsed.get("dest_uids"):
            _log(f"dm {chat.get('room_name') or chat.get('room_id')}: {text}")
        else:
            _log(f"all: {text}")
    else:
        _log("identidad enviada")


def _emit(sock, out: str, dry_run: bool) -> bool:
    """Send one outgoing event; dry run prints chats instead of sending."""
    try:
        parsed = parse_event(out)
        if dry_run and parsed is not None and parsed.get("type") == "b-t-f":
            print(out, flush=True)
            return True
        sock.sendall(out.encode("utf-8"))
        return True
    except (OSError, ssl.SSLError):
        return False


def _reader(sock, inbox: queue.Queue, sentinel) -> None:
    buf = ""
    try:
        while True:
            data = sock.recv(65536)
            if not data:
                break
            buf += data.decode("utf-8", errors="replace")
            events, buf = split_stream(buf)
            for xml in events:
                parsed = parse_event(xml)
                if parsed is not None:
                    inbox.put(parsed)
    except Exception:
        pass
    finally:
        inbox.put(sentinel)


def _serve(sock, bot: Bot, dry_run: bool) -> None:
    inbox: queue.Queue = queue.Queue()
    sentinel = object()
    thread = threading.Thread(
        target=_reader, args=(sock, inbox, sentinel), daemon=True,
        name="tak-reader",
    )
    thread.start()
    disconnected = False
    while not disconnected:
        tick_start = time.monotonic()
        while True:
            try:
                item = inbox.get_nowait()
            except queue.Empty:
                break
            if item is sentinel:
                disconnected = True
                break
            now = datetime.now(timezone.utc)
            chat = item.get("chat")
            if chat and parse_command(chat.get("text") or "") is not None:
                who = chat.get("sender_callsign") or chat.get("sender_uid")
                word = (chat.get("text") or "").strip().split()
                _log(f"cmd {who} {word[0][:20] if word else ''}")
            for out in bot.handle_event(item, now):
                if _emit(sock, out, dry_run):
                    _log_outgoing(out)
                else:
                    disconnected = True
                    break
        if not disconnected:
            now = datetime.now(timezone.utc)
            for out in bot.tick(now):
                if _emit(sock, out, dry_run):
                    _log_outgoing(out)
                else:
                    disconnected = True
                    break
        if not disconnected:
            elapsed = time.monotonic() - tick_start
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)


def _backoff(failures: int) -> int:
    return _BACKOFFS[min(failures, len(_BACKOFFS) - 1)]


def run(args) -> int:
    workdir = tempfile.mkdtemp(prefix="tak-mando-")
    os.chmod(workdir, 0o700)
    stop = threading.Event()
    try:
        info = read_package(args.package)
        host = getattr(args, "host", None) or info.host
        port = getattr(args, "port", None) or info.port
        zones = load_zones(args.zones)
        places = load_places(args.zones)
        share_path = getattr(args, "share_package", None)
        package = None
        if share_path:
            url_base = (
                getattr(args, "share_url_base", None)
                or f"https://{info.host}:8443"
            )
            share_name = (
                getattr(args, "share_name", None) or "Paquete del campo"
            )
            package = shared_package(share_path, share_name, url_base)
        share_state = getattr(args, "share_state", None)
        sent_state = Path(share_state) if share_state else None
        lat = args.lat
        lon = args.lon
        if args.no_weather:
            cache = ForecastCache(lambda: [])
        else:
            cache = ForecastCache(lambda: fetch_forecast(lat, lon))

            def _weather() -> None:
                try:
                    cache.get(datetime.now(timezone.utc))
                except Exception:
                    pass
                while not stop.wait(300):
                    try:
                        cache.get(datetime.now(timezone.utc))
                    except Exception:
                        pass

            threading.Thread(
                target=_weather, daemon=True, name="tak-weather"
            ).start()
        bot = Bot(
            uid=args.uid,
            callsign=args.callsign,
            lat=lat,
            lon=lon,
            utc_offset_h=args.tz_offset,
            zones=zones,
            places=places,
            lost_after=args.lost_after,
            geofence_cooldown=args.geofence_cooldown,
            rain_mm=args.rain_mm,
            announce=not args.no_announce,
            ignore_prefixes=tuple(args.ignore_prefix),
            forecast_cache=cache,
            package=package,
            sent_state=sent_state,
            grid=getattr(args, "grid", None),
        )
        _log(
            f"zonas: {len(zones)} peligros, {len(places)} lugares "
            f"({lat:.5f},{lon:.5f})"
        )
        if package is not None:
            _log(f"paquete: {package.name} ({package.filename}, "
                 f"{package.size_bytes} bytes)")
        failures = 0
        while True:
            try:
                sock = tls_connect(info, host, port, Path(workdir), args.openssl)
            except Exception as exc:
                wait = _backoff(failures)
                failures += 1
                _log(f"sin conexión ({exc}), reintento en {wait} s")
                time.sleep(wait)
                continue
            failures = 0
            _log(f"conectado a {host}:{port}")
            try:
                now = datetime.now(timezone.utc)
                ident = bot.identity(now)
                sock.sendall(ident.encode("utf-8"))
                _log("identidad enviada")
                _serve(sock, bot, args.dry_run)
            except (OSError, ssl.SSLError) as exc:
                _log(f"error de conexión ({exc})")
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
            wait = _backoff(failures)
            failures += 1
            _log(f"desconectado, reintento en {wait} s")
            time.sleep(wait)
    except KeyboardInterrupt:
        return 0
    finally:
        stop.set()
        shutil.rmtree(workdir, ignore_errors=True)
    return 0
