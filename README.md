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
WorkingDirectory=/opt/tak-mando
ExecStart=/usr/bin/python3 -m mando /etc/tak-mando/mando.zip --zones /etc/tak-mando/campo.geojson

[Install]
WantedBy=multi-user.target
```

Install it with:

```sh
sudo cp deploy/tak-mando.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tak-mando
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

## License

AGPL-3.0-or-later, see [LICENSE](LICENSE).
