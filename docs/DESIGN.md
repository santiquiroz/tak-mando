# tak-mando design

tak-mando is a companion bot for a TAK server (OpenTAKServer, FreeTAKServer, TAK Server). It connects as one more client with its own certificate, listens to everything the team shares and adds what a server alone cannot:

- **Geofence safety alerts**: a direct chat message to a player the moment they enter a hazard polygon (a flooded tank, a cliff edge).
- **Chat commands**: players type `!luz`, `!clima`, `!equipo`, `!donde <callsign>`, `!peligros` in ATAK/iTAK chat and get an answer.
- **Announcements**: sunset, last light and darkness, and heavy rain in the next hours, sent to the whole team.
- **Lost contact**: a team message when a player who was active stops reporting.

User-facing text is Spanish. Python 3.10+, standard library only. No shell, no eval. Every module below except `bot.py` and `__main__.py` is pure and fully unit tested.

All coordinates in GeoJSON are `[lon, lat]`. All other function arguments are `lat, lon` in that order. Times are timezone-aware `datetime` in UTC unless a name says `local`.

---

## mando/cot.py - Cursor-on-Target build and parse

- `cot_time(dt) -> str`: UTC ISO with milliseconds, `2026-10-10T21:00:00.000Z`.
- `xml_escape(text) -> str`: escapes `& < > " '` (as `&amp; &lt; &gt; &quot; &apos;`).
- `identity_event(uid, callsign, lat, lon, now, stale_s=300, version="0.1.0") -> str`: makes the bot a contact players can tap and chat with.
  ```xml
  <event version="2.0" uid="UID" type="a-f-G-E-S" how="h-g-i-g-o" time="T" start="T" stale="T+stale_s">
    <point lat="LAT" lon="LON" hae="9999999.0" ce="9999999.0" le="9999999.0"/>
    <detail>
      <contact callsign="CALLSIGN" endpoint="*:-1:stcp"/>
      <__group name="Cyan" role="HQ"/>
      <takv device="tak-mando" platform="tak-mando" os="python" version="VERSION"/>
      <remarks>Bot del servidor. Escribe !ayuda en el chat.</remarks>
    </detail>
  </event>
  ```
  Coordinates formatted with 7 decimals.
- `chat_event(sender_uid, sender_callsign, room_name, room_id, text, now, dest_uid=None, message_id=None) -> str`: GeoChat, exactly the shape OpenTAKServer itself emits. `message_id` defaults to `str(uuid.uuid4())`.
  ```xml
  <event version="2.0" uid="GeoChat.SENDER_UID.ROOM_ID.MID" type="b-t-f" how="h-g-i-g-o" time="T" start="T" stale="T+3600s">
    <point lat="0.0" lon="0.0" hae="9999999.0" ce="9999999.0" le="9999999.0"/>
    <detail>
      <__chat parent="RootContactGroup" groupOwner="false" messageId="MID" chatroom="ROOM_NAME" id="ROOM_ID" senderCallsign="SENDER_CALLSIGN">
        <chatgrp uid0="SENDER_UID" uid1="ROOM_ID" id="ROOM_ID"/>
      </__chat>
      <link uid="SENDER_UID" type="a-f-G-U-C" relation="p-p"/>
      <remarks source="BAO.F.ATAK.SENDER_UID" to="ROOM_ID" time="T">TEXT</remarks>
      <marti><dest uid="DEST_UID"/></marti>        <!-- only when dest_uid is given -->
    </detail>
  </event>
  ```
  OpenTAKServer routes `<marti><dest uid=…/>` to exactly one device (each client is bound by its uid), for ATAK and iTAK alike.
- `dm_event(sender_uid, sender_callsign, to_uid, to_callsign, text, now, message_id=None) -> str`: `chat_event(..., room_name=to_callsign, room_id=to_uid, dest_uid=to_uid)`.
- `all_chat_event(sender_uid, sender_callsign, text, now, message_id=None) -> str`: room name and id both `All Chat Rooms`, no dest.
- `parse_event(xml: str) -> dict | None`: returns `None` for malformed XML and for any text containing `<!DOCTYPE` or `<!ENTITY` (entity-expansion attacks; check before parsing). Result keys:
  - `uid`, `type`, `how` (str, may be empty)
  - `time`, `stale` (aware datetime or None; accept `Z` with or without fractional seconds)
  - `lat`, `lon` (float or None)
  - `callsign` (from `detail/contact@callsign`, or None)
  - `chat`: None, or a dict `{room_name, room_id, sender_uid, sender_callsign, text, message_id}` when `detail/__chat` exists and `detail/remarks` has text. `sender_uid` comes from `chatgrp@uid0`, falling back to `link@uid`.
  - `dest_uids`: list of `uid` attributes of `detail/marti/dest`.
- `split_stream(buffer: str) -> tuple[list[str], str]`: cuts every complete `<event …>…</event>` out of an accumulated socket buffer and returns them with the unfinished remainder. Anything before the first `<event` (an XML declaration, keepalive bytes) is dropped. If the remainder grows beyond 1 000 000 characters without a closing tag, return it as `""` (a hostile or broken peer must not exhaust memory).

## mando/geo.py - distances and shapes

- `haversine_m(lat1, lon1, lat2, lon2) -> float` (Earth radius 6 371 008.8 m).
- `bearing_deg(lat1, lon1, lat2, lon2) -> float` initial bearing, normalised to `[0, 360)`.
- `cardinal_es(deg) -> str`: 8 points in Spanish, each 45° wide centred on its direction: `N NE E SE S SO O NO`.
- `point_in_ring(lat, lon, ring) -> bool`: ring is a list of `[lon, lat]`, closed or not (ray casting).
- `distance_to_ring_m(lat, lon, ring) -> float`: 0 when inside, otherwise the shortest distance to any edge, computed in a local equirectangular projection around the point.
- `centroid(ring) -> tuple[float, float]`: `(lat, lon)` mean of the distinct vertices (ignore a closing duplicate).
- `format_distance(m) -> str`: under 100 m round to 5 m (`"35 m"`), under 1000 m round to 10 m (`"120 m"`), else kilometres with one decimal and a comma (`"1,2 km"`).
- `describe_offset(from_lat, from_lon, to_lat, to_lon) -> str`: `"120 m al NE"`; when under 5 m return `"aquí mismo"`.

## mando/sun.py - light

- `sun_events(day: date, lat, lon, utc_offset_h: float) -> dict[str, datetime | None]`: NOAA solar position algorithm. Keys: `astronomical_dawn, nautical_dawn, civil_dawn, sunrise, sunset, civil_dusk, nautical_dusk, astronomical_dusk`. Zenith angles 108°, 102°, 96°, 90.833°. Values are naive local datetimes (UTC + offset); None when the event does not happen that day.
- `moon_illumination(dt_utc) -> float`: fraction 0..1 from the moon age, using the reference new moon 2000-01-06 18:14 UTC and a synodic month of 29.530588853 days: `(1 - cos(2π·age/period)) / 2`.
- Acceptance (Neira, Caldas: lat 5.1606, lon -75.4918, offset -5, 2026-10-10): sunrise 05:48 ±2 min, sunset 17:49 ±2 min, civil dusk 18:12 ±3 min, nautical dusk 18:36 ±3 min; moon illumination at 2026-10-10 23:00 UTC below 0.03.

## mando/weather.py - Open-Meteo forecast

- `Hour` dataclass: `time` (aware UTC), `temp_c`, `rain_prob` (int %), `rain_mm`, `cloud` (int %), `visibility_m`, `gust_kmh` (floats; None when missing).
- `forecast_url(lat, lon) -> str`: `https://api.open-meteo.com/v1/forecast?latitude=LAT&longitude=LON&hourly=temperature_2m,precipitation_probability,precipitation,cloud_cover,visibility,wind_gusts_10m&timezone=UTC&forecast_days=3` (4 decimals for lat/lon).
- `parse_forecast(data: dict) -> list[Hour]`: reads `data["hourly"]`; times like `2026-10-10T21:00` are UTC. Skip entries whose time does not parse. Missing arrays mean None values.
- `next_hours(hours, now_utc, n) -> list[Hour]`: the first `n` hours whose time is at or after `now_utc` floored to the hour.
- `fetch_forecast(lat, lon, timeout=10) -> list[Hour]`: `urllib.request` GET with header `User-Agent: tak-mando`; raises on any error.
- `ForecastCache(fetcher, ttl_s=1800)`: `get(now_utc) -> list[Hour]` returns cached data and refetches (by calling `fetcher()`) when older than `ttl_s`. A failed fetch keeps the old data, stores the exception text in `last_error` and does not retry before another `ttl_s / 6` seconds. `fetched_at` holds the time of the last good fetch (None before).

## mando/zones.py - the field file

The same GeoJSON used for the map overlay (simplestyle properties, `folder` property for grouping).

- `Zone` dataclass: `name`, `ring` (list of `[lon, lat]`), `message`.
- `load_zones(path, alert_folders=("Peligros",)) -> list[Zone]`: every Polygon whose `properties.folder` is in `alert_folders`, or that has a non-empty `properties.alert`. `message` = `alert`, else `description`, else `""`. Invalid JSON raises `ValueError`.
- `Place` dataclass: `name`, `lat`, `lon`, `ring` (list or None).
- `load_places(path) -> list[Place]`: every named Point (lat/lon from it) and Polygon (centroid plus ring); skip LineStrings and any feature whose folder starts with `Curvas`.
- `nearest_place(places, lat, lon) -> tuple[Place, float] | None`: if the point is inside one or more polygons, the smallest one (by bounding-box area) at distance 0; otherwise the place with the smallest distance (polygon edge distance, or point distance).
- `describe_location(places, lat, lon) -> str`: `"en Bloque central"` when inside, `"a 40 m de Silo 2"` when nearer than 150 m, otherwise `""`.

## mando/roster.py - who is where

- `Player` dataclass: `uid, callsign, lat, lon, last_seen` (aware UTC), `stale` (aware or None).
- `Roster(ignore_uids=(), ignore_prefixes=("overlay-",))`:
  - `update(event: dict, now) -> Player | None`: accepts events whose `type` starts with `a-f-G-U`, with a callsign, and lat/lon not None and not both 0. Ignores ignored uids and prefixes. Stores and returns the player.
  - `players() -> list[Player]` sorted by callsign (case-insensitive).
  - `get(uid) -> Player | None`.
  - `find(query) -> Player | None`: exact case-insensitive callsign match first, then a unique case-insensitive prefix match, else None.

## mando/rules.py - when to speak

- `GeofenceTracker(zones, cooldown_s=180)`: `check(player, now) -> list[str]`. A message for each zone the player is inside now but was not inside on the previous check for that player (first check counts as "was outside"), unless the same player was alerted for the same zone less than `cooldown_s` ago. Message: `"⚠ PELIGRO: {zone.name}. {zone.message}"` (drop the trailing part when the message is empty).
- `LostContactTracker(after_s=300, recent_s=3600)`: `check(players, now) -> list[Player]` newly lost players: `last_seen` older than `after_s` but newer than `recent_s`. Each player is returned once per disappearance; it is re-armed when the player reports again (`last_seen` newer than the moment it was flagged). `after_s=0` disables (always `[]`).
- `light_schedule(events: dict, moon: float) -> list[tuple[datetime, str]]` (local times, sorted), from a `sun_events` dict, skipping missing keys:
  - `sunset - 15 min`: `"Puesta del sol en 15 min ({sunset:%H:%M}). Ocupen posiciones nocturnas con la última luz."`
  - `sunset`: `"Se puso el sol. Oscuridad total hacia las {nautical_dusk:%H:%M}."`
  - `nautical_dusk`: `"Oscuridad total. Luna al {moon:.0%}. Disciplina de luz: solo luz roja, pantallas al mínimo."`
  - `sunrise - 15 min`: `"Amanece a las {sunrise:%H:%M}."`
- `Announcer(grace_s=600)`: `due(schedule, now_local) -> list[str]`: texts whose time is `<= now_local` and not older than `grace_s`, each returned once (key: time + text).
- `RainWatch(threshold_mm=2.0, min_interval_s=7200)`: `check(hours, now_utc, utc_offset_h) -> str | None`: if any of the next 2 hours (by `next_hours`) has `rain_mm >= threshold`, return `"Lluvia fuerte prevista hacia las {HH:MM local} ({mm:.1f} mm/h). Protejan equipos y radares."` (the first such hour), at most once per `min_interval_s`.

## mando/commands.py - chat commands

- `parse_command(text) -> tuple[str, str] | None`: None unless the stripped text starts with `!`. Returns `(name, args)` with the name lower-cased and accents removed, mapped through aliases: `ayuda|help|h → ayuda`, `luz|sol → luz`, `clima|tiempo → clima`, `equipo|team → equipo`, `donde|ubicar → donde`, `peligros|peligro → peligros`; anything else → `desconocido`. `args` is the rest, stripped, max 40 characters.
- `Context` dataclass: `now_utc, utc_offset_h, requester` (Player or None), `players` (list), `places`, `zones`, `sun` (sun_events dict for today), `moon` (float), `hours` (forecast list), `forecast_age_s` (float or None).
- `run_command(name, args, ctx) -> str` (reply at most 700 characters; cut with `…`):
  - `ayuda`: `"Comandos: !luz (sol y oscuridad) · !clima (próximas 3 h) · !equipo (dónde está cada uno) · !donde <callsign> · !peligros (cerca de ti)"`.
  - `luz`: before sunset `"Sol se pone {HH:MM} (en {Xh Ym}). Oscuridad total {HH:MM}. Luna {N} %."`; after nautical dusk and before sunrise `"Es de noche. Amanece {HH:MM} (en {…}). Luna {N} %."`; between sunset and nautical dusk `"Crepúsculo. Oscuridad total {HH:MM} (en {…})."`. Durations like `"1 h 12 min"` or `"8 min"`.
  - `clima`: next 3 hours as `"{HH:MM} {t:.0f}°C lluvia {p}% {mm:.1f} mm"` joined by `" · "`; append ` (pronóstico de hace {N} min)` when `forecast_age_s` is known; `"Sin pronóstico: el servidor no pudo consultarlo."` when empty.
  - `equipo`: other players (not the requester), at most 10, each `"{callsign} {describe_offset from requester} (hace {age})"`; without a requester position just `"{callsign} (hace {age})"`. Age: `"12 s"`, `"3 min"`, `"1 h"`. Empty: `"No hay nadie más reportando posición."`.
  - `donde`: no args → `"Uso: !donde <callsign>"`; not found → `"No encuentro a '{args}'. Conectados: {callsigns}"`; found → `"{callsign}: {describe_offset from requester}{, cerca/en place} (hace {age})"` (skip the offset when the requester position is unknown).
  - `peligros`: zones within 200 m of the requester sorted by `distance_to_ring_m`, `"{name} a {describe_offset to centroid}"` joined by `" · "` (inside: `"{name}: ESTÁS DENTRO"`); none: `"Ningún peligro marcado a menos de 200 m."`; unknown requester position: `"No tengo tu posición todavía."`.
  - `desconocido`: `"No conozco ese comando. Escribe !ayuda."`.

## mando/bot.py and mando/__main__.py - runtime

CLI: `python -m mando PACKAGE.zip --zones FIELD.geojson [--host H] [--port P] [--callsign Mando] [--uid mando-bot] [--tz-offset -5] [--lat LAT --lon LON] [--lost-after 300] [--geofence-cooldown 180] [--rain-mm 2.0] [--no-announce] [--no-weather] [--dry-run] [--openssl openssl]`.

- **Connection**: read the OpenTAKServer/ATAK connection package (zip, possibly nested): the `.pref` gives `connectString0` (`host:port:ssl`) and `clientPassword` (default `atakatak`); two `.p12` files, the one with `truststore` in its name is the CA. Convert both with `openssl pkcs12 -legacy -in … -passin env:MANDO_P12_PASSWORD` (the password goes through the environment, never the command line) (`-nodes` for the client, `-nokeys` for the truststore) into a private temporary directory (mode 0700) that is deleted on exit. `ssl.SSLContext(PROTOCOL_TLS_CLIENT)`, `check_hostname = False` (TAK server certificates do not carry the connect address), verify against the truststore, load the client chain.
- **Position of the bot**: `--lat/--lon`, defaulting to the centre of the zones file bounding box.
- **Reader thread**: receive, decode UTF-8 with `errors="replace"`, `split_stream`, `parse_event`, put dicts on a `queue.Queue`.
- **Main loop (1 s tick)**:
  - drain the queue: `Roster.update`; for each updated player run `GeofenceTracker.check` and send each message as a DM.
  - chat events whose text parses as a command, not sent by the bot itself, at most one command per sender every 3 s: build the `Context`, run the command, reply by DM when the command came by DM (`room_id` equals the bot uid), otherwise to `All Chat Rooms`.
  - every 60 s: re-send the identity event.
  - every 30 s: `LostContactTracker.check` → one All Chat Rooms message per player: `"⚠ {callsign} lleva {N} min sin reportar. Última posición: {describe_location or lat,lon with 5 decimals}."`
  - every 60 s (unless `--no-announce`): `Announcer.due(light_schedule(today), now_local)` and `RainWatch.check` → All Chat Rooms.
  - weather (unless `--no-weather`): `ForecastCache.get` from a background thread every 5 min so the loop never blocks on the network.
- **Reconnect** with backoff 5, 10, 20, 40, 60, 60 … seconds; keep roster and trackers across reconnects.
- **Dry run**: connect and listen, but print outgoing chat events instead of sending them (identity is still sent so the bot is visible).
- **Logging**: one line per action to stdout with `flush=True` (`HH:MM:SS dm Recon: ⚠ PELIGRO …`, `HH:MM:SS cmd thomas !luz`). Never log certificate material.
- Ctrl+C exits 0 and removes the temporary directory.
