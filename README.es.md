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
  `!donde <callsign>`, `!peligros`, `!mapas`, `!cuadro` en el chat de
  ATAK/iTAK y reciben respuesta.
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

## Entregar el paquete del campo

El bot puede entregar el paquete de datos del campo (mapas sin conexión,
capa táctica) a cada jugador, para que nadie pase archivos zip:

1. Sube primero el zip a los **paquetes de datos del servidor**
   (interfaz web de OpenTAKServer). El bot no sube el archivo: enlaza la
   copia del servidor por su sha256, así que los mismos bytes ya deben
   estar ahí.
2. El puerto TCP **8443** (sincronización Marti) del servidor debe ser
   alcanzable por los jugadores; ATAK/iTAK descarga el paquete por ese
   puerto.
3. Apunta el bot a una copia local del mismo zip:

```sh
python -m mando mando.zip --zones campo.geojson \
  --share-package campo.zip --share-name "Paquete del campo" \
  --share-state /var/lib/tak-mando/sent.json
```

La primera vez que un jugador reporta posición, el bot le envía el paquete
una vez, con un mensaje directo que explica cómo aceptarlo. Quien no lo
recibió puede escribir `!mapas` para recibirlo de nuevo. `--share-url-base`
reemplaza el enlace de descarga (por defecto `https://<servidor>:8443` del
paquete de conexión); `--share-state` recuerda a quién ya se envió entre
reinicios.

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

## Cuadrícula

Pasa `--grid NORTH,WEST,CELL_M,COLS,ROWS` (por ejemplo
`5.1650,-75.4960,100,9,9`) para tender una cuadrícula GRG sobre el terreno:
columnas A, B, C… de oeste a este y filas 1, 2, 3… de norte a sur. El bot
añade entonces el cuadro a las respuestas de `!donde`, a las líneas de
`!equipo` y a los avisos de pérdida de contacto.

Los jugadores pueden escribir `!cuadro` (alias `!grid`, `!cuadricula`,
`!yo`) para saber en qué cuadro están, por ejemplo
`Estás en E5, a 40 m de Torre sur.`

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
EnvironmentFile=-/home/ots/.config/tak-mando/llm.env
WorkingDirectory=/opt/tak-mando
ExecStart=/usr/bin/python3 -m mando /etc/tak-mando/mando.zip --zones /etc/tak-mando/campo.geojson --layer /var/lib/tak-mando/juego.geojson --state /var/lib/tak-mando/mando-state.json --status /var/lib/tak-mando/mando-status.json --dted /var/lib/tak-mando/campo.dt2 --exposure /var/lib/tak-mando/exposure.json --buildings /var/lib/tak-mando/edificios.geojson --admin-uid ANDROID-xxxxxxxx --llm-url auto --event-name "OP MEDUSA"

[Install]
WantedBy=multi-user.target
```

Instalación:

```sh
sudo cp deploy/tak-mando.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tak-mando
```

El cerebro necesita una clave API. Guárdala en el archivo que nombra `EnvironmentFile`, con `MANDO_LLM_KEY=...` dentro y modo 600:

```sh
printf 'MANDO_LLM_KEY=...\n' > /home/ots/.config/tak-mando/llm.env
chmod 600 /home/ots/.config/tak-mando/llm.env
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
| `!mapas` | Envía el paquete de mapas del campo. |
| `!cuadro` | Tu cuadro del mapa (GRG). |

Los comandos funcionan en All Chat Rooms y por mensaje directo al bot; el
bot responde en la misma sala. Máximo un comando por remitente cada
3 segundos.

## Cerebro con IA y capa de juego

Con `--llm-url`, Mando también entiende lenguaje natural y edita una capa de juego compartida. Sin esa bandera, el bot se comporta exactamente como antes.

Escríbele a Mando por mensaje directo. Ejemplos:

- `marca un punto de reunión EXFIL ALFA en E5`
- `¿qué hay en D6?`
- `¿dónde está Recon?`
- `sitrep`
- `BRAVO es nuestro`
- `en 20 min avisa que cierra el objetivo`

En All Chat Rooms solo los mensajes que empiezan con `Mando,`, `Mando:` o `Mando ` llegan al cerebro. Los comandos `!` siguen funcionando con o sin cerebro.

Permisos: los autorizados escriben directamente. Las ediciones de los demás se vuelven propuestas numeradas en la carpeta `Propuestas`, y cada autorizado recibe un mensaje directo que termina en `#N → responde ok N o no N`. Responde `ok N` o `no N` para aceptar o descartar una propuesta. `!autorizar <callsign>` y `!desautorizar <callsign>` dan y quitan acceso (solo autorizados). Los uids autorizados iniciales vienen de `--admin-uid` (repetible). El permiso se revisa en el código según el uid del remitente, nunca por el modelo.

Archivos: `--layer` es el GeoJSON de la capa de juego. La carpeta `Juego` guarda los objetos compartidos. La carpeta `Propuestas` guarda las propuestas pendientes. Los ids son `j-N`. `--state` guarda uids autorizados, propuestas, avisos programados y el historial de `deshacer`. `--status` es una foto de solo lectura que se escribe cada 10 s. Un servicio de overlay publica la capa en TAK leyendo archivos GeoJSON (por ejemplo tak-overlay.py del proyecto Blindside, que acepta varios archivos y republica unos 2 s después de un cambio).

Instalación:

```sh
python -m mando mando.zip --zones campo.geojson --grid 5.1650,-75.4960,100,9,9 \
  --layer juego.geojson --state mando-state.json --status mando-status.json \
  --admin-uid ANDROID-xxxxxxxx --llm-url auto --llm-model claude-sonnet-4-6 \
  --event-name "OP MEDUSA"
```

`--llm-model` vale `claude-sonnet-4-6` por defecto. La clave API viene de la variable de entorno `MANDO_LLM_KEY` (nunca se registra en el log). Sirve cualquier endpoint OpenAI-compatible `/chat/completions` con llamadas a herramientas (bipolar-code, OpenAI, un servidor local llama.cpp u Ollama con herramientas). `--llm-url auto` significa `http://<puerta de enlace por defecto>:8000/v1`, que es el host Windows cuando el bot corre en WSL2. Los ejemplos usan el campo OP MEDUSA en una cuadrícula de 9x9 cuadros de 100 m (columnas A-I de oeste a este, filas 1-9 de norte a sur).

Límites: 4 s entre mensajes, 40 por hora por jugador, 300 por hora en total, 5 ediciones del mapa por mensaje, 45 s por respuesta. Ante cualquier falla, Mando responde `Sin cerebro ahora, usa !ayuda.`

MCP: las mismas herramientas por stdio JSON-RPC, siempre autorizado:

```sh
python -m mando.mcp --layer juego.geojson --state mando-state.json --status mando-status.json --zones campo.geojson --grid 5.1650,-75.4960,100,9,9
```

Ejemplo con Claude Code:

```sh
claude mcp add --scope user mando -- wsl.exe -d Ubuntu-24.04 -u ots --cd /path/to/tak-mando -- python3 -m mando.mcp --layer juego.geojson --state mando-state.json --status mando-status.json --zones campo.geojson --grid 5.1650,-75.4960,100,9,9
```

### Herramientas de terreno

- `linea_de_vista`: "¿me ven desde la torre sur si estoy en D7?" Línea de vista sobre un archivo DTED2 (1 segundo de arco). La altura del observador viene de las alturas de `--buildings`, nunca del texto del jugador. Responde visible o bloqueado, y dónde lo tapa el terreno.
- `reportar_contacto`: "contacto, 3 enemigos en E6". Cualquier jugador puede reportar. El punto recibe un símbolo militar estándar (infantería `a-h-G-U-C-I`, vehículo `a-h-G-E-V`, dron `a-h-A-M-F-Q`, francotirador, desconocido `a-u-G`). Se anuncia a todos y se borra solo después de 10 minutos. Un reporte cada 20 s por jugador.
- `ruta_cubierta`: "ruta cubierta de la llegada a la nave central". A* sobre una cuadrícula de exposición (cuántas torres ven cada celda de 10 m). Dibuja la ruta menos expuesta como una línea. Los no autorizados reciben una propuesta.
- `marcar_punto` también acepta los tipos `aliado` y `desconocido`. `enemigo`, `aliado` y `desconocido` usan símbolos militares.

Opciones para el bot y el MCP: `--dted PATH` (DTED2 `.dt2`), `--exposure PATH` (`exposure.json`), `--buildings PATH` (GeoJSON con polígonos de edificios y `height_m`). Los archivos ausentes o rotos nunca detienen el bot. La herramienta responde que el dato no está cargado.

`tak/terrain.py` del proyecto Blindside genera estos archivos desde el modelo de elevación gratuito Copernicus GLO-30. Atribución: "Copernicus GLO-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided by ESA (Copernicus)".

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
- El modelo solo puede lo que permiten las herramientas, y cada escritura
  se revisa por uid en el código.
- El texto de otros jugadores nunca llega al modelo, salvo callsigns y
  nombres de objetos, limpios y recortados.
- Mantén el endpoint del LLM fuera de la LAN y de internet.

## Licencia

AGPL-3.0-or-later, ver [LICENSE](LICENSE).
