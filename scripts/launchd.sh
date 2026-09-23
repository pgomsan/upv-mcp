#!/bin/bash
# Agente de launchd que ejecuta upv-publish cada hora, sin intervencion.
#
#   scripts/launchd.sh instalar https://upv-feed.<subdominio>.workers.dev/entregas.ics
#   scripts/launchd.sh estado
#   scripts/launchd.sh desinstalar
#
# La URL es la del PUT (/entregas.ics), que no es secreta. La del feed del movil
# (/<FEED_PATH>.ics) NO va aqui: el plist es texto plano. El token tampoco: el
# publicador lo lee del Llavero en cada pasada.
#
# Cada hora es barato: PoliformaT solo se consulta cuando caduca la cache (6 h), y
# solo se sube algo al Worker si el feed ha cambiado. Si el Mac esta dormido,
# launchd lo ejecuta al despertar.
set -euo pipefail

LABEL="es.upv-mcp.publish"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$REPO/.venv/bin/upv-publish"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/upv-publish.log"
DOMINIO="gui/$(id -u)"

instalar() {
	local url="${1:-}"
	if [[ ! "$url" =~ ^https://[^/]+/entregas\.ics$ ]]; then
		echo "Uso: $0 instalar https://upv-feed.<subdominio>.workers.dev/entregas.ics" >&2
		echo "Es la URL del PUT (acaba en /entregas.ics), NO la del feed del movil." >&2
		exit 1
	fi
	if [[ ! -x "$BIN" ]]; then
		(cd "$REPO" && uv sync)
	fi

	mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
	cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$BIN</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$REPO</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>UPV_FEED_URL</key>
		<string>$url</string>
		<key>PATH</key>
		<string>/usr/bin:/bin:/usr/sbin:/sbin</string>
	</dict>
	<key>StartInterval</key>
	<integer>3600</integer>
	<key>RunAtLoad</key>
	<true/>
	<key>ProcessType</key>
	<string>Background</string>
	<key>StandardOutPath</key>
	<string>$LOG</string>
	<key>StandardErrorPath</key>
	<string>$LOG</string>
</dict>
</plist>
EOF
	plutil -lint "$PLIST" >/dev/null

	# Reinstalar sobre uno ya cargado: primero se descarga el viejo.
	launchctl bootout "$DOMINIO/$LABEL" 2>/dev/null || true
	launchctl bootstrap "$DOMINIO" "$PLIST"
	echo "Instalado: $PLIST"
	echo "Se ejecuta ahora y despues cada hora. Log: $LOG"
}

desinstalar() {
	launchctl bootout "$DOMINIO/$LABEL" 2>/dev/null || true
	rm -f "$PLIST"
	echo "Desinstalado. El log se conserva en $LOG"
}

estado() {
	if ! launchctl print "$DOMINIO/$LABEL" >/dev/null 2>&1; then
		echo "No esta instalado."
		exit 1
	fi
	launchctl print "$DOMINIO/$LABEL" | grep -E "^\s*(state|runs|last exit code|run interval)\s*=" || true
	echo "--- ultimas lineas de $LOG"
	tail -n 12 "$LOG" 2>/dev/null || echo "(sin log todavia)"
}

case "${1:-}" in
instalar) instalar "${2:-}" ;;
desinstalar) desinstalar ;;
estado) estado ;;
*)
	echo "Uso: $0 {instalar <url-del-PUT>|estado|desinstalar}" >&2
	exit 1
	;;
esac
