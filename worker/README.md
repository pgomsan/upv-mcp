# upv-feed: Worker que sirve el .ics de entregas

`upv-publish` (en el paquete Python) genera `build/entregas.ics` y lo sube aqui con
un PUT. El calendario del movil se suscribe al GET.

| Peticion | Respuesta |
|---|---|
| `PUT /entregas.ics` con `Authorization: Bearer <FEED_TOKEN>` | `204`; `401` token malo; `413` > 1 MB; `400` si no es un VCALENDAR |
| `GET /<FEED_PATH>.ics` | el .ics, `text/calendar; charset=utf-8`, `Cache-Control: max-age=300` |
| cualquier otra cosa | `404` sin cuerpo |

## La URL del feed es un secreto

iOS no manda cabeceras de autenticacion en una suscripcion de calendario, asi que
**quien tenga la URL del GET ve todas tus entregas**. Tratala como una contrasena:

- `FEED_PATH` es un *secret* de Wrangler, nunca una `var` de `wrangler.jsonc` (las
  vars se commitean) ni un literal en el codigo.
- No la pegues en issues, chats, capturas ni commits. En los comandos de abajo se
  genera en una variable de shell y se guarda en el Llavero, sin pasar por la
  pantalla.
- Los invocation logs y las trazas del Worker estan desactivados porque guardan la
  URL completa. `npx wrangler tail` y `npx wrangler dev` SI la imprimen: no
  compartas su salida.
- Si se filtra, rotala (ultima seccion) y vuelve a suscribirte.

`FEED_TOKEN` solo protege el PUT; lo usa `upv-publish` desde el Llavero.

## Puesta en marcha

Todo desde `worker/`, en este orden. Nada de esto lo ejecuta el repo por ti.

```bash
cd worker
npm install
npx wrangler login
```

### 1. Namespace KV

```bash
npx wrangler kv namespace create FEED
```

Copia el `id` que imprime en `wrangler.jsonc`, sustituyendo
`RELLENAR_CON_EL_ID_DEL_NAMESPACE`. El id no es secreto: se puede commitear.

### 2. Secrets

Los dos valores se generan en variables y van directos a Cloudflare y al Llavero,
sin quedar en el historial ni en la pantalla. Si `wrangler secret put` pregunta si
crear el Worker porque aun no existe, contesta que si.

```bash
FEED_TOKEN=$(openssl rand -hex 32)
printf '%s' "$FEED_TOKEN" | npx wrangler secret put FEED_TOKEN
security add-generic-password -U -a upv-mcp -s upv-feed-token -w "$FEED_TOKEN"
unset FEED_TOKEN

FEED_PATH=$(openssl rand -hex 32)
printf '%s' "$FEED_PATH" | npx wrangler secret put FEED_PATH
security add-generic-password -U -a upv-mcp -s upv-feed-path -w "$FEED_PATH"
unset FEED_PATH
```

El Worker rechaza un `FEED_PATH` de menos de 32 caracteres o con algo distinto de
`[A-Za-z0-9_-]` (responde 404 a todo), y un `FEED_TOKEN` de menos de 32 (500 al
PUT). `upv-feed-path` en el Llavero no lo usa ningun programa: es para que puedas
recuperar la URL de suscripcion.

### 3. Deploy

```bash
npx wrangler deploy
```

Imprime la URL del Worker, `https://upv-feed.<tu-subdominio>.workers.dev`.

### 4. Publicador

`upv-publish` lee la URL del PUT de `UPV_FEED_URL` (no es secreta: sin token no
sirve de nada):

```bash
export UPV_FEED_URL="https://upv-feed.<tu-subdominio>.workers.dev/entregas.ics"
uv run upv-publish --dry-run   # imprime el .ics, no escribe ni sube nada
uv run upv-publish             # genera build/entregas.ics y lo sube
```

Para que se ejecute solo cada hora (y al despertar el Mac), instala el agente de
launchd desde la raiz del repo:

```bash
scripts/launchd.sh instalar "https://upv-feed.<tu-subdominio>.workers.dev/entregas.ics"
scripts/launchd.sh estado        # ultima ejecucion y ultimas lineas del log
scripts/launchd.sh desinstalar
```

El plist solo lleva la URL del PUT. El token se lee del Llavero en cada pasada y
la URL del feed no aparece en ningun sitio. Log: `~/Library/Logs/upv-publish.log`.

Si CAS rechaza tu contrasena (p.ej. porque la cambiaste), el agente deja de hacer
login hasta que ejecutes `upv-mcp-config set poliformat`: repetir un login
rechazado cada hora bloquearia tu cuenta UPV. Lo veras en el log.

### 5. Suscribirse desde el iPhone

Recupera la ruta del Llavero y monta la URL en tu Mac, no en un chat:

```bash
echo "https://upv-feed.<tu-subdominio>.workers.dev/$(security find-generic-password -a upv-mcp -s upv-feed-path -w).ics"
```

Ajustes > Calendario > Cuentas > Anadir cuenta > Otra > Anadir calendario suscrito.
Los clientes refrescan a su ritmo (el feed pide una hora con `X-PUBLISHED-TTL`).

## Desarrollo local

```bash
printf 'FEED_TOKEN=%s\nFEED_PATH=%s\n' "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" > .dev.vars
npx wrangler dev --local
npm run check        # wrangler types + tsc --noEmit
```

`.dev.vars` esta en el `.gitignore`. Tras cambiar `wrangler.jsonc`, vuelve a
ejecutar `npx wrangler types`.

## Rotar la URL si se filtra

```bash
FEED_PATH=$(openssl rand -hex 32)
printf '%s' "$FEED_PATH" | npx wrangler secret put FEED_PATH
security add-generic-password -U -a upv-mcp -s upv-feed-path -w "$FEED_PATH"
unset FEED_PATH
```

La URL vieja deja de responder al momento (404). Borra la suscripcion del movil y
crea otra con la URL nueva. Para rotar el token, lo mismo con `FEED_TOKEN` y
`upv-feed-token`.
