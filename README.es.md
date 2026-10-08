# tak-mando

Bot acompañante para un servidor TAK (OpenTAKServer, FreeTAKServer,
TAK Server). Se conecta como un cliente más con su propio certificado,
escucha lo que comparte el equipo y aporta lo que un servidor solo no
puede. Los mensajes del bot están en español. El diseño está en
[docs/DESIGN.md](docs/DESIGN.md).

## Qué hace

- **Alertas de geocerca**: mensaje directo al jugador en el momento en que
  entra a un polígono de peligro (un tanque inundado, el borde de un
  barranco).
- **Comandos de chat**: los jugadores escriben `!luz`, `!clima`, `!equipo`,
  `!donde <callsign>`, `!peligros` en el chat de ATAK/iTAK y reciben
  respuesta.
- **Avisos**: puesta del sol, última luz y oscuridad total, además de
  lluvia fuerte en las próximas horas, enviados a todo el equipo.
- **Pérdida de contacto**: mensaje al equipo cuando un jugador que estaba
  activo deja de reportar.

## Requisitos

- Python 3.10+, solo biblioteca estándar (sin paquetes pip).
- La herramienta `openssl` en la línea de comandos con el proveedor
  legacy, usada una vez por conexión para convertir los certificados del
  paquete (`openssl pkcs12 -legacy`).

## Instalación con OpenTAKServer

1. Crea un usuario para el bot (por ejemplo `mando`) en la interfaz web
   de OpenTAKServer y descarga su paquete de conexión (`.zip`).
2. Copia el `.zip` a la máquina que correrá el bot.
3. **Borra después el paquete del servidor**: OpenTAKServer publica los
   paquetes de conexión como paquetes de datos para todos los usuarios,
   así que dejar ahí el paquete del bot entregaría su certificado a todo
   el equipo. Elimínalo de los paquetes de datos del servidor una vez
   descargado.

`--host`/`--port` reemplazan la dirección `connectString0` del paquete
cuando el bot alcanza al servidor por otra interfaz.

## El GeoJSON del terreno

Un solo archivo alimenta la capa del mapa y el bot (`--zones`). Usa
propiedades simplestyle y la propiedad `folder` para agrupar:

- Cada **polígono** en la carpeta `"Peligros"`, o con una propiedad
  `"alert"` no vacía, se vuelve zona de peligro. El texto del aviso es
  `alert`, si no `description`, si no vacío. Entrar a una dispara un
  mensaje directo: `⚠ PELIGRO: <nombre>. <mensaje>`.
- Cada punto o polígono **con nombre** da nombre a un lugar, usado en las
  respuestas de `!donde` y en las últimas posiciones conocidas. Las líneas
  y los elementos cuya carpeta empieza con `Curvas` se omiten.

La posición del bot es por defecto el centro del bounding box de todas
las coordenadas de este archivo (`--lat`/`--lon` la reemplazan).

## Ejecución

```sh
python -m mando mando.zip --zones campo.geojson
```

Banderas útiles (ver `python -m mando --help` para todas):

```sh
python -m mando mando.zip --zones campo.geojson \
  --callsign Mando --uid mando-bot --tz-offset -5 \
  --lost-after 300 --geofence-cooldown 180 --rain-mm 2.0 \
  --ignore-prefix overlay- --dry-run
```

- `--no-announce`: no envía avisos de luz ni lluvia.
- `--no-weather`: nunca consulta el pronóstico (tampoco en segundo plano).
- `--dry-run`: conecta y escucha, pero muestra los chats salientes en vez
  de enviarlos (la identidad sí se envía para que el bot siga visible).

## systemd

Hay una unidad de ejemplo en [deploy/tak-mando.service](deploy/tak-mando.service):

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

Instalación:

```sh
sudo cp deploy/tak-mando.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tak-mando
```

## Comandos de chat

| Comando | Respuesta |
|---|---|
| `!ayuda` | Lista de comandos. |
| `!luz` | Puesta del sol, oscuridad total y luna. |
| `!clima` | Pronóstico de las próximas 3 horas. |
| `!equipo` | Dónde está cada jugador y hace cuánto reportó. |
| `!donde <callsign>` | Distancia, dirección y lugar de un jugador. |
| `!peligros` | Zonas de peligro a menos de 200 m de ti. |

Los comandos funcionan en All Chat Rooms y por mensaje directo al bot; el
bot responde en la misma sala. Máximo un comando por remitente cada
3 segundos.

## Notas de seguridad

- El bot solo lee lo que el equipo ya comparte en el servidor TAK; no
  agrega ninguna recolección nueva.
- La verificación del nombre del servidor en TLS está desactivada (los
  certificados TAK no traen la dirección de conexión), pero la cadena del
  servidor siempre se verifica contra la CA del paquete y el bot se
  autentica con su propio certificado cliente.
- La contraseña del paquete se pasa a `openssl` por variable de entorno,
  nunca en la línea de comandos, para que no aparezca en la lista de
  procesos. Los certificados viven en un directorio temporal privado
  (modo 0700) que se borra al salir, y nunca se registran en el log.
- Sin shell ni eval: los subprocesos usan solo listas de argumentos.

## Licencia

AGPL-3.0-or-later, ver [LICENSE](LICENSE).
