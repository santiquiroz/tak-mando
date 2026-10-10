# tak-mando

Companion bot for a TAK server (OpenTAKServer, FreeTAKServer, TAK Server).
It connects as one more client with its own certificate, listens to what
the team shares, and adds what a server alone cannot. Bot replies are in
Spanish. Design details live in [docs/DESIGN.md](docs/DESIGN.md).

## What it does

- **Geofence safety alerts**: a direct chat message to a player the moment
  they enter a hazard polygon (a flooded tank, a cliff edge).
- **Chat commands**: players type `!luz`, `!clima`, `!equipo`,
  `!donde <callsign>`, `!peligros`, `!mapas`, `!cuadro` in ATAK/iTAK chat
  and get an answer.
- **Announcements**: sunset, last light and darkness, plus heavy rain in
  the next hours, sent to the whole team.
- **Lost contact**: a team message when a player who was active stops
  reporting.

## Requirements

- Python 3.10+, standard library only (no pip packages).
- The `openssl` command line tool with the legacy provider, used once per
  connection to convert the package certificates (`openssl pkcs12 -legacy`).

## Setup with OpenTAKServer

1. Create a user for the bot (for example `mando`) in the OpenTAKServer
   web interface and download its connection package (`.zip`).
2. Copy the `.zip` to the machine that will run the bot.
3. **Delete the package from the server afterwards**: OpenTAKServer
   publishes connection packages as data packages to every user, so leaving
   the bot's package there would hand its certificate to the whole team.
   Remove it from the server's data packages once downloaded.

`--host`/`--port` override the `connectString0` address from the package
when the bot reaches the server through a different interface.

## The field GeoJSON

One file drives both the map overlay and the bot (`--zones`). It uses
simplestyle properties and a `folder` property for grouping:

- Every **Polygon** in folder `"Peligros"`, or with a non-empty `"alert"`
  property, becomes a hazard zone. The alert text is `alert`, else
  `description`, else empty. Entering one triggers a direct message:
  `⚠ PELIGRO: <name>. <message>`.
- Every **named** Point or Polygon names a place, used for `!donde`
  answers and last-known positions. LineStrings and features whose folder
  starts with `Curvas` are skipped.

The bot's own position defaults to the centre of the bounding box of all
coordinates in this file (`--lat`/`--lon` override it).

## Sharing the field package

The bot can hand the field data package (offline maps, tactical overlay)
to every player, so nobody has to pass zip files around:

1. Upload the zip to the **server's data packages** first (OpenTAKServer
   web interface). The bot does not upload the file: it links to the
   server copy by its sha256, so the same bytes must already be there.
2. TCP port **8443** (Marti sync) on the server must be reachable by the
   players; ATAK/iTAK downloads the package from that port.
3. Point the bot at a local copy of the same zip:

```sh
python -m mando mando.zip --zones campo.geojson \
  --share-package campo.zip --share-name "Paquete del campo" \
  --share-state /var/lib/tak-mando/sent.json
```

The first time a player reports position, the bot sends them the package
once, with a direct message explaining how to accept it. Players who
missed it can type `!mapas` to get it again. `--share-url-base` overrides
the download link (default `https://<server>:8443` from the connection
package); `--share-state` remembers who already got it across restarts.

## Running

```sh
python -m mando mando.zip --zones campo.geojson
```

Useful flags (see `python -m mando --help` for all of them):

```sh
python -m mando mando.zip --zones campo.geojson \
  --callsign Mando --uid mando-bot --tz-offset -5 \
  --lost-after 300 --geofence-cooldown 180 --rain-mm 2.0 \
  --ignore-prefix overlay- --dry-run
```

- `--no-announce`: skip light and rain announcements.
- `--no-weather`: never query the forecast (background thread included).
- `--dry-run`: connect and listen, but print outgoing chats instead of
  sending them (identity is still sent so the bot stays visible).

## Grid references

Pass `--grid NORTH,WEST,CELL_M,COLS,ROWS` (for example
`5.1650,-75.4960,100,9,9`) to lay a square GRG over the field: columns
lettered A, B, C… west to east and rows numbered 1, 2, 3… north to south.
The bot then adds the square to `!donde` answers, `!equipo` lines and
lost-contact messages.

When the organizers hand out their own grid (rotated, rectangular cells,
printed labels that may even repeat a letter), describe it in a JSON file and
pass `--grid-file PATH` instead (it wins over `--grid`, bot and MCP alike):

```json
{"north": 5.1614426, "west": -75.4895678, "cell_m": 25.66, "row_m": 20.44,
 "cols": 15, "rows": 17, "col_bearing_deg": 207.73, "row_bearing_deg": 297.41,
 "labels": "ABCDEFGHIJGKLMN"}
```

`north`/`west` is the outer corner of the first cell; columns grow along
`col_bearing_deg` in `cell_m` steps and rows along `row_bearing_deg` in
`row_m` steps. A repeated letter resolves to its first column when a player
types it, and the bot writes the later one as `G10 (2ª G)`.

Players can ask `!cuadro` (aliases `!grid`, `!cuadricula`, `!yo`) for the
square they are in, for example `Estás en E5, a 40 m de Torre sur.`

## systemd

An example unit is shipped as [deploy/tak-mando.service](deploy/tak-mando.service):

```ini
[Unit]
Description=tak-mando companion bot for the TAK server
After=network-online.target
Wants=network-online.target

[Service]
User=ots
Restart=always
RestartSec=15
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/home/ots/.config/tak-mando/llm.env
WorkingDirectory=/opt/tak-mando
ExecStart=/usr/bin/python3 -m mando /etc/tak-mando/mando.zip --zones /etc/tak-mando/campo.geojson --layer /var/lib/tak-mando/juego.geojson --state /var/lib/tak-mando/mando-state.json --status /var/lib/tak-mando/mando-status.json --dted /var/lib/tak-mando/campo.dt2 --exposure /var/lib/tak-mando/exposure.json --buildings /var/lib/tak-mando/edificios.geojson --admin-uid ANDROID-xxxxxxxx --llm-url auto --event-name "OP MEDUSA"

[Install]
WantedBy=multi-user.target
```

Install it with:

```sh
sudo cp deploy/tak-mando.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tak-mando
```

The brain needs an API key. Keep it in the file named by `EnvironmentFile`, with `MANDO_LLM_KEY=...` inside and mode 600:

```sh
printf 'MANDO_LLM_KEY=...\n' > /home/ots/.config/tak-mando/llm.env
chmod 600 /home/ots/.config/tak-mando/llm.env
```

## Chat commands

| Comando | Respuesta |
|---|---|
| `!ayuda` | Lista de comandos. |
| `!luz` | Puesta del sol, oscuridad total y luna. |
| `!clima` | Pronóstico de las próximas 3 horas. |
| `!equipo` | Dónde está cada jugador y hace cuánto reportó. |
| `!donde <callsign>` | Distancia, dirección y lugar de un jugador. |
| `!peligros` | Zonas de peligro a menos de 200 m de ti. |
| `!mapas` | Envía el paquete de mapas del campo. |
| `!cuadro` | Tu cuadro del mapa (GRG). |

Commands work in All Chat Rooms and by direct message to the bot; the bot
answers in the same room. At most one command per sender every 3 seconds.

## AI brain and game layer

With `--llm-url` set, Mando also understands plain language and edits a shared game layer. Without it, the bot behaves exactly as before.

Write to Mando by direct message. Examples:

- `marca un punto de reunión EXFIL ALFA en E5`
- `¿qué hay en D6?`
- `¿dónde está Recon?`
- `sitrep`
- `BRAVO es nuestro`
- `en 20 min avisa que cierra el objetivo`

In All Chat Rooms only messages that start with `Mando,`, `Mando:` or `Mando ` reach the brain. `!` commands keep working with or without the brain.

Permissions: authorized players write directly. Edits from anyone else become numbered proposals in the `Propuestas` folder, and each authorized player gets a direct message ending in `#N → responde ok N o no N`. Reply `ok N` or `no N` to accept or discard a proposal. `!autorizar <callsign>` and `!desautorizar <callsign>` grant and revoke access (authorized only). Initial authorized uids come from repeatable `--admin-uid`. Permission is checked in code from the sender uid, never by the model.

Files: `--layer` is the game layer GeoJSON. Folder `Juego` holds the shared objects. Folder `Propuestas` holds pending proposals. Object ids look like `j-N`. `--state` holds authorized uids, proposals, scheduled announcements and the history behind `deshacer`. `--status` is a read-only snapshot written every 10 s. An overlay service publishes the layer to TAK by reading GeoJSON files (for example tak-overlay.py from the Blindside project, which accepts several files and republishes within about 2 s of a change).

Setup:

```sh
python -m mando mando.zip --zones campo.geojson --grid 5.1650,-75.4960,100,9,9 \
  --layer juego.geojson --state mando-state.json --status mando-status.json \
  --admin-uid ANDROID-xxxxxxxx --llm-url auto --llm-model claude-sonnet-4-6 \
  --event-name "OP MEDUSA"
```

`--llm-model` defaults to `claude-sonnet-4-6`. The API key comes from the environment variable `MANDO_LLM_KEY` (never logged). Any OpenAI-compatible `/chat/completions` endpoint with tool calling works (bipolar-code, OpenAI, a local llama.cpp or Ollama server with tools). `--llm-url auto` means `http://<default gateway>:8000/v1`, which is the Windows host when the bot runs in WSL2. The examples assume the OP MEDUSA field on a 9x9 grid of 100 m squares (columns A-I west to east, rows 1-9 north to south).

Limits: 4 s between messages, 40 per hour per player, 300 per hour in total, 5 map edits per message, 45 s per answer. On any failure Mando answers `Sin cerebro ahora, usa !ayuda.`

MCP: the same tools over stdio JSON-RPC, always authorized:

```sh
python -m mando.mcp --layer juego.geojson --state mando-state.json --status mando-status.json --zones campo.geojson --grid 5.1650,-75.4960,100,9,9
```

Claude Code example:

```sh
claude mcp add --scope user mando -- wsl.exe -d Ubuntu-24.04 -u ots --cd /path/to/tak-mando -- python3 -m mando.mcp --layer juego.geojson --state mando-state.json --status mando-status.json --zones campo.geojson --grid 5.1650,-75.4960,100,9,9
```

### Terrain tools

- `linea_de_vista`: "¿me ven desde la torre sur si estoy en D7?" Line of sight over a DTED2 file (1 arc-second). The observer height comes from the building heights in `--buildings`, never from player text. It answers visible or blocked, and where the terrain blocks it.
- `reportar_contacto`: "contacto, 3 enemigos en E6". Any player can report. The point gets a standard military symbol (infantry `a-h-G-U-C-I`, vehicle `a-h-G-E-V`, drone `a-h-A-M-F-Q`, sniper, unknown `a-u-G`). It is announced to everyone and removed automatically after 10 minutes. One report every 20 s per player.
- `ruta_cubierta`: "ruta cubierta de la llegada a la nave central". A* over an exposure grid (how many watchtowers see each 10 m cell). It draws the least exposed path as a line. Unauthorized players get a proposal.
- `marcar_punto` also takes the `aliado` and `desconocido` types. `enemigo`, `aliado` and `desconocido` use military symbols.

Options for the bot and the MCP: `--dted PATH` (DTED2 `.dt2`), `--exposure PATH` (`exposure.json`), `--buildings PATH` (GeoJSON with building polygons and `height_m`). Missing or broken files never stop the bot. The tool answers that the data is not loaded.

`tak/terrain.py` from the Blindside project generates these files from the free Copernicus GLO-30 elevation model. Attribution: "Copernicus GLO-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided by ESA (Copernicus)".

## Security notes

- The bot only reads what the team already shares on the TAK server; it
  adds no new collection.
- TLS hostname check is disabled (TAK server certificates do not carry the
  connect address), but the server chain is always verified against the
  package CA and the bot authenticates with its own client certificate.
- The package password is passed to `openssl` through an environment
  variable, never on the command line, so it does not show in the process
  list. Certificates live in a private temporary directory (mode 0700)
  that is deleted on exit, and are never logged.
- No shell, no eval: subprocess calls use argument lists only.
- The model can only do what the tools allow, and every write is
  permission-checked by uid in code.
- Text from other players never reaches the model, except callsigns and
  object names, cleaned and cut.
- Keep the LLM endpoint off the LAN and the internet.

## License

AGPL-3.0-or-later, see [LICENSE](LICENSE).
