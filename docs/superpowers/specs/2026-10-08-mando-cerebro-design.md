# Mando con cerebro: IA que conversa y edita el mapa

Fecha: 2026-10-08 · Estado: aprobado (enfoque 1) · Meta: probado el 2026-10-09 en la noche, en uso el 2026-10-10.

## 1. Objetivo

Mando ya es un contacto del mapa que responde comandos `!` y avisa peligros. Ahora, además, entiende lenguaje natural y edita una capa de juego compartida:

- Un jugador le escribe por mensaje directo "marca EXFIL ALFA en E5" y el punto aparece en el mapa de todos en segundos.
- "¿Qué hay en D6?", "¿dónde está Recon?", "dame un SITREP": responde con datos reales del servidor y nunca inventa posiciones.
- Los organizadores manejan objetivos (estado y color), avisos programados ("en 20 min avisa que cierra el objetivo") y anuncios.
- Desde el PC, Claude Code (o cualquier cliente MCP) usa las mismas herramientas sobre la misma capa.

Criterios de éxito:

1. "marca X en E5" de un autorizado → marcador en el mapa de un ATAK conectado en ≤ 10 s.
2. Un jugador no autorizado pide lo mismo → queda como propuesta numerada, los autorizados reciben aviso, y "ok N" de un autorizado la publica.
3. Sin bipolar (caído, sin cuota o lento) → Mando contesta "Sin cerebro ahora, usa !ayuda" y todo lo existente (comandos `!`, geocerca, anuncios, paquete) sigue igual.
4. Desde Claude Code, la herramienta MCP `marcar_punto` publica en la misma capa, y el bot ve el cambio.
5. Una zona de peligro dibujada en la capa de juego dispara la alerta de geocerca como las del archivo base.

## 2. Alcance

Dentro: cerebro LLM en el bot, herramientas de lectura y escritura, capa `juego.geojson`, permisos con propuestas, avisos programados, registro de eventos para SITREP, servidor MCP por stdio, cambios mínimos en el servicio de mapa de Blindside y la red Windows↔WSL para alcanzar a bipolar.

Fuera: puntaje automático por presencia (solo una parte de los jugadores usa TAK, el bot no ve a los demás), voz, imágenes, varias partidas a la vez, edición de la capa base `cementera.geojson`.

## 3. Arquitectura

```
ATAK/iTAK ──CoT──► OpenTAKServer ◄──CoT── tak-mando (WSL, systemd)
                        ▲                    │  comandos ! (como hoy)
                        │                    │  mensaje libre → cerebro (hilo aparte)
                        │                    │        └─HTTP─► bipolar :8000 (Windows) ─► proveedor LLM
                        │                    │  herramientas ─► layer.py ─► juego.geojson + mando-state.json
       blindside-overlay (WSL) ◄── lee ──────┘                         ▲
       publica cementera.geojson + juego.geojson                        │
                                                Claude Code ─stdio─► mando.mcp (mismas herramientas)
```

- `juego.geojson` es la única fuente de verdad de la capa de juego. Escriben el bot y el MCP, siempre a través de `mando/layer.py`. Publica el servicio de mapa de Blindside, que ya sabe crear, actualizar y borrar objetos.
- `mando-state.json` guarda lo que no es mapa: uids autorizados, propuestas pendientes, avisos programados y el contador de propuestas.
- `mando-status.json` es una foto de solo lectura que el bot escribe cada 10 s (jugadores y últimos eventos), para que el MCP pueda contestar "dónde está" y SITREP sin estar conectado al servidor TAK.

## 4. Formato de la capa de juego

GeoJSON FeatureCollection con las propiedades que el servicio de mapa ya entiende (`name`, `folder`, `description`, `marker-color`, `stroke`, `stroke-width`, `fill`, `fill-opacity`), más:

| Propiedad | Valores | Uso |
|---|---|---|
| `id` | `j-<n>` (n entero creciente) | uid estable en TAK (`overlay-j-<n>`), referencia para mover/borrar |
| `folder` | `Juego` o `Propuestas` | capa oficial o pendiente |
| `kind` | `punto`, `zona`, `objetivo`, `peligro` | estilo y comportamiento |
| `tipo` | `objetivo`, `peligro`, `reunion`, `medico`, `spawn`, `enemigo`, `info` | sólo puntos |
| `status` | `libre`, `nuestro`, `enemigo`, `disputado` | sólo objetivos |
| `author` | callsign | quién lo creó o pidió |
| `author_uid` | uid | para `deshacer` |
| `created`, `updated` | ISO UTC | registro |
| `proposal` | entero | sólo en `Propuestas` |

`kind` de un punto es `objetivo` si su `tipo` es `objetivo`, y `punto` en cualquier otro caso. El de una zona es el argumento `kind` de `dibujar_zona`. `estado_objetivo` actúa sobre cualquier objeto con `kind` `objetivo`. La geocerca sólo usa polígonos con `kind` `peligro` de la carpeta Juego: las propuestas nunca disparan alertas.

Colores fijos: peligro `#ff3b30`, objetivo según estado (libre `#ffffff`, nuestro `#34c759`, enemigo `#ff3b30`, disputado `#ffcc00`), reunión `#32d2ff`, médico `#ff2d55`, spawn `#5856d6`, enemigo `#ff9500`, info `#8e8e93`. Propuestas siempre `#ff9500` con `fill-opacity` 0.1. Las zonas son círculos guardados como Polygon de 24 vértices (radio 10–300 m), así el servicio de mapa y la geocerca las tratan igual que cualquier polígono.

## 5. Componentes

Todo es Python 3.10+ con sólo la librería estándar, igual que el resto de tak-mando. Los módulos nuevos son puros salvo donde se indica, y llevan pruebas unitarias.

### 5.1 `mando/layer.py`: la capa y el estado compartido

- `Layer(path, state_path)`: operaciones con candado (`fcntl.flock` sobre `<path>.lock`) y escritura atómica (archivo temporal + `os.replace`). Cada operación lee, modifica y escribe dentro del candado, así el bot y el MCP no se pisan.
- `add_feature(props, geometry, now, actor_uid) -> dict`, `update_feature(fid, changes, now, actor_uid) -> dict`, `move_feature(fid, lat, lon, now, actor_uid) -> dict` (desplaza toda la geometría), `delete_feature(fid, actor_uid) -> dict`, `features(folder=None) -> list[dict]`.
- Historial en el estado: cada escritura agrega `{uid, action, fid, before}` (`before` es la feature completa antes del cambio, o `null` si fue una creación). Se guardan las últimas 100 entradas. `undo_last(actor_uid) -> str` deshace la última entrada de ese uid: si fue una creación, borra; si fue un cambio o un movimiento, restaura `before`; si fue un borrado, vuelve a crear `before`. Una sola vez por entrada.
- Estado: `authorized() -> set[str]`, `authorize(uid)`, `revoke(uid)`, `add_proposal(op, args, author, author_uid, now) -> int`, `pop_proposal(n) -> dict | None`, `proposals() -> list[dict]`, `add_announcement(at_utc, text, audience) -> int`, `due_announcements(now) -> list[dict]` (los devuelve una sola vez y los quita).
- `circle(lat, lon, radius_m, n=24) -> list[[lon, lat]]` (anillo cerrado).
- Archivo inexistente = capa vacía. JSON inválido → `ValueError` sin tocar el archivo.

### 5.2 `mando/places.py`: resolver "lugar"

- `resolve_place(text, ctx) -> Resolved | str`: devuelve `Resolved(lat, lon, label)` o un mensaje de error en español. Acepta:
  - cuadro de la cuadrícula `"E5"` (centro de la celda);
  - número o nombre de edificio del GRG (`"12"`, `"torre sur"`, sin tildes ni mayúsculas);
  - callsign de un jugador con posición (`"Recon"`);
  - `"aquí"`, `"mi posición"`, `"donde estoy"` (posición de quien pide);
  - `"lat,lon"` con decimales.
- Rechaza lugares fuera de la cuadrícula del campo con un margen de 500 m.

### 5.3 `mando/events.py`: registro para SITREP

- `EventLog(maxlen=300)`: `add(kind, text, now)`, `since(now, minutes) -> list[Event]`. Clases: `conexion`, `contacto_perdido`, `peligro`, `mapa`, `propuesta`, `aviso`.
- El bot registra: primer reporte de un jugador, contacto perdido, alertas de geocerca, cada escritura en la capa, propuestas y anuncios.

### 5.4 `mando/tools.py`: herramientas

- `TOOLS`: lista de esquemas en formato OpenAI `{"type": "function", "function": {name, description, parameters}}`, con descripciones en español.
- `execute(name, args, actor, ctx) -> str`: valida los argumentos, revisa el permiso y devuelve el texto que verá el modelo (máx. 600 caracteres). Nunca lanza excepciones hacia el cerebro: cualquier error se devuelve como texto.
- `actor`: `Actor(uid, callsign, authorized: bool, is_mcp: bool)`. El MCP siempre está autorizado.

Lectura (cualquiera):

| Herramienta | Argumentos | Devuelve |
|---|---|---|
| `donde_esta` | `callsign` | cuadro, edificio o lugar cercano, antigüedad del reporte |
| `que_hay_en` | `lugar` | edificios, zonas, peligros y objetos de la capa en ese cuadro o a menos de 60 m |
| `estado_equipo` | (ninguno) | jugadores con cuadro y antigüedad; perdidos marcados |
| `peligros` | (ninguno) | zonas de peligro, base y de juego, con distancia si el actor tiene posición |
| `luz_y_clima` | (ninguno) | el texto de `!luz` y `!clima` |
| `sitrep` | `minutos` (1–60, por defecto 10) | eventos del registro, agrupados |
| `capa_juego` | (ninguno) | objetos de la capa con id, nombre, tipo, estado y cuadro, más propuestas pendientes |

Escritura:

| Herramienta | Argumentos | Efecto |
|---|---|---|
| `marcar_punto` | `nombre`, `lugar`, `tipo`, `nota?` | punto en Juego |
| `dibujar_zona` | `nombre`, `lugar`, `radio_m` (10–300), `kind` (`zona`, `peligro`, `objetivo`), `nota?` | círculo en Juego |
| `estado_objetivo` | `objetivo` (id o nombre), `estado` | cambia estado y color |
| `mover` | `objeto` (id o nombre), `lugar` | mueve |
| `borrar` | `objeto` (id o nombre) | borra de Juego |
| `deshacer` | (ninguno) | `undo_last` del actor |
| `programar_aviso` | `minutos` (1–240), `texto`, `para` (`todos` o `autorizados`) | aviso futuro |
| `anunciar` | `texto` | mensaje a todo el chat ahora |
| `confirmar_propuesta` | `numero`, `aceptar` (bool) | ejecuta o descarta una propuesta |

Permisos (los revisa el código según `actor`, nunca el modelo):

- Autorizado o MCP: ejecuta todo directamente.
- No autorizado: `marcar_punto`, `dibujar_zona`, `estado_objetivo`, `mover` y `borrar` se convierten en propuesta. Las creaciones se dibujan en la carpeta Propuestas para que se vean. Mando le avisa por mensaje directo a cada autorizado conectado: `"Recon propone: marcar EXFIL ALFA (reunión) en E5. #3 → responde ok 3 o no 3"`. `deshacer` sólo actúa sobre lo suyo (sus propuestas). `programar_aviso`, `anunciar` y `confirmar_propuesta` se rechazan con una frase clara.
- Límites por mensaje: máx. 5 escrituras; textos y nombres recortados (nombre 40, nota 200, aviso 300 caracteres) y sin caracteres de control.

### 5.5 `mando/brain.py`: el cerebro

- `LlmClient(base_url, api_key, model, timeout_s=25)`: `chat(messages, tools) -> dict` con POST `{base_url}/chat/completions` (`urllib`), `Authorization: Bearer`. Lanza `LlmError` ante cualquier error HTTP, de red, timeout o JSON inválido.
- `Brain(client, tools_executor, system_prompt, max_rounds=4, memory_turns=6, memory_ttl_s=900)`: `answer(actor, text, ctx, now) -> str`. Bucle: modelo → si pide herramientas, ejecutarlas (con el `actor` real) y devolver los resultados → hasta que conteste texto o se cumplan `max_rounds` (entonces responde con lo último útil). Memoria por uid: las últimas `memory_turns` vueltas de usuario y asistente, sin resultados de herramientas, que caducan a los `memory_ttl_s`.
- `system_prompt(field) -> str`: rol (asistente táctico de una partida de airsoft; respuestas de máx. 3 frases y en español; usar herramientas para cualquier dato; nunca inventar posiciones; nombrar lugares por cuadro y edificio), definición de la cuadrícula (columnas A–I de oeste a este, filas 1–9 de norte a sur, 100 m), lista compacta de edificios del GRG con su cuadro y nombres de los peligros. Se arma una vez al arrancar.
- Respuesta final: máx. 700 caracteres, recortada con `…`.

### 5.6 Cambios en `mando/bot.py`

- Mensajes con `!`: igual que hoy.
- Nuevo: `ok N` / `no N` de un autorizado → `confirmar_propuesta` directo, sin modelo.
- Nuevo: `!autorizar <callsign>` y `!desautorizar <callsign>`, sólo para autorizados. Los uids autorizados iniciales se pasan con `--admin-uid` (repetible) y quedan en el estado.
- Mensaje directo a Mando sin `!` → cerebro. En All Chat, sólo si empieza con `mando` seguido de `,`, `:` o espacio.
- El cerebro corre en un hilo aparte (`ThreadPoolExecutor(max_workers=2)`); `Bot.drain(now) -> list[str]` entrega las respuestas listas y el bucle principal la llama en cada vuelta. Así una respuesta lenta del modelo nunca frena la geocerca.
- Límite por jugador: una consulta en curso a la vez, 4 s mínimo entre mensajes y máx. 40 por hora. Global: máx. 300 por hora. Al pasarse: `"Dame un respiro, prueba en un minuto."`.
- Si falla el cerebro (`LlmError`, timeout total de 45 s o sin configurar): `"Sin cerebro ahora, usa !ayuda."`.
- La geocerca suma las zonas `peligro` de la capa de juego: recarga `juego.geojson` cuando cambia su mtime (comprobado en `tick`).
- `tick` también publica los avisos programados vencidos, avisa a los autorizados de propuestas nuevas que haya creado el MCP y escribe `mando-status.json` cada 10 s.
- Opciones nuevas en `__main__.py`: `--layer PATH`, `--state PATH`, `--status PATH`, `--admin-uid UID` (repetible), `--llm-url URL|auto`, `--llm-model NAME` (por defecto `claude-sonnet-4-6`, el alias que bipolar enruta al proveedor activo). `--llm-url auto` usa `http://<puerta de enlace por defecto>:8000/v1`, leída de `/proc/net/route` (la IP de Windows vista desde WSL cambia al reiniciar). La clave sale de la variable de entorno `MANDO_LLM_KEY`. Sin `--llm-url`, el cerebro queda apagado y el bot se comporta como hoy.

### 5.7 `mando/mcp.py`: servidor MCP

- stdio, JSON-RPC 2.0, una línea por mensaje. Métodos: `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`.
- Mismas herramientas que el cerebro, con `actor` MCP (autorizado). Las de lectura que necesitan jugadores leen `mando-status.json`; si es más viejo de 60 s, lo dicen.
- `anunciar` y `programar_aviso` se guardan en `mando-state.json` y los publica el bot en su siguiente `tick`.
- Uso: `python3 -m mando.mcp --layer ... --state ... --status ... --zones ... --grid ...`. Se registra en Claude Code con `claude mcp add mando -- wsl -d Ubuntu-24.04 -u ots -- python3 -m mando.mcp ...` (cwd del repo).

### 5.8 Servicio de mapa de Blindside (`tak/tak-overlay.py`)

- `geojson` pasa a aceptar varios archivos (`nargs="+"`), compatible con la llamada actual de un archivo.
- Durante la espera entre ciclos, revisa cada 2 s el mtime de los archivos y publica de inmediato si alguno cambió.
- El servicio `blindside-overlay` se reinstala con `cementera.geojson juego.geojson`.

## 6. Red y despliegue

- bipolar escucha hoy sólo en `127.0.0.1:8000` y WSL usa NAT: el bot no lo alcanza. Cambio: `start-bipolar.ps1` usa `--host 0.0.0.0`, más una regla de firewall de Windows **Block** TCP 8000 entrante desde todo lo que no sea `127.0.0.0/8` ni `172.16.0.0/12` (en Windows, una regla de bloqueo gana sobre las de permitir, y hoy Python tiene permitido el entrante en perfiles público y privado). Verificar desde WSL (`curl http://<gw>:8000/api/health` → 200) y desde otro equipo de la LAN (debe fallar).
- tak-mando: archivo de entorno `/home/ots/.config/tak-mando/llm.env` (permisos 600) con `MANDO_LLM_KEY`, más `EnvironmentFile=` en la unidad systemd.
- Archivos de la partida: `juego.geojson`, `mando-state.json` y `mando-status.json` en `~/.blindside/field/` (privado, fuera de los repos).
- Que bipolar arranque con Windows es una tarea aparte del repo bipolar-code, no de este spec.

## 7. Seguridad

- El modelo sólo puede lo que permiten las herramientas, y el permiso se decide por uid en el código. Si un jugador le dice al modelo "ignora las reglas", no gana permisos de escritura.
- Las cadenas que controla un jugador (callsign, nombres, notas) se recortan y limpian antes de entrar a resultados de herramientas y a CoT (escape XML como hoy). No hay herramienta de "borrar todo"; `deshacer` existe y toda escritura queda en el registro.
- El texto de otros jugadores nunca entra al contexto del modelo, salvo callsigns y nombres de objetos, recortados.
- El MCP no abre puertos (sólo stdio local). La clave de bipolar no se registra en logs.
- bipolar no queda expuesto a la LAN ni a internet (regla de bloqueo; el router no reenvía el 8000).

## 8. Pruebas

- Unitarias: `layer` (candado, atómico, ids, círculo, propuestas, avisos), `places` (cada forma de lugar y rechazos), `events`, `tools` (cada herramienta, permisos y conversión a propuesta, límites, textos largos y caracteres de control), `brain` (cliente falso con llamadas guionizadas: una herramienta, varias rondas, `max_rounds`, `LlmError`, memoria y caducidad), bot (enrutamiento `!` / cerebro / `ok N` / All Chat con y sin "mando", `drain`, límites, recarga de peligros por mtime) y `mcp` (proceso por stdio: initialize, tools/list, tools/call).
- Overlay: varios archivos y publicación por cambio de mtime.
- Punta a punta: jugador falso contra el servidor real con bipolar real (estilo `e2e_mando.py`): un autorizado marca → sale el evento del overlay con el nombre; un no autorizado → propuesta y aviso al autorizado → `ok N` → publicado.
- En el celular: el marcador aparece en el ATAK y en el iTAK.

## 9. Riesgos

| Riesgo | Mitigación |
|---|---|
| El modelo gratuito (`gpt-oss-20b`) elige mal la herramienta | Pocas herramientas, descripciones cortas, `lugar` resuelto en código; probar con frases reales antes del viernes; se puede cambiar el modelo con `--llm-model` |
| Cuota del proveedor de bipolar | Límites por jugador y global; el resto del bot sigue sin cerebro |
| Internet de la casa cae en la partida | El servidor TAK también depende de eso; sin cerebro, el bot sigue con lo determinista |
| La IP de Windows vista desde WSL cambia | `--llm-url auto` la lee en cada arranque |
| Escrituras simultáneas bot y MCP | `flock` más escritura atómica en `layer.py` |
