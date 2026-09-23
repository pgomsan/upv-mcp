"""CLI de configuracion: guarda las URL iCal en el llavero del sistema.

Existe para que instalar el servidor no obligue a escribir un token en un fichero.
No imprime nunca el secreto completo.
"""

from __future__ import annotations

import argparse
import sys
from getpass import getpass

from upv_mcp.config import (
    EXAMS_KEY,
    KEYRING_SERVICE,
    POLIFORMAT_PASSWORD_KEY,
    POLIFORMAT_USER_KEY,
    SCHEDULE_KEY,
    delete_secret,
    load_settings,
    read_secret,
    write_secret,
)

_CALENDARS = {"schedule": SCHEDULE_KEY, "exams": EXAMS_KEY}

#: Objetivo especial: no es una URL, son dos secretos (usuario + contrasena).
_POLIFORMAT = "poliformat"


def _mask(value: str) -> str:
    """Muestra lo justo para reconocer la URL sin revelar el token."""
    if len(value) <= 24:
        return value[:8] + "..."
    return f"{value[:32]}...{value[-4:]} ({len(value)} chars)"


def _read_url(calendar: str) -> str:
    """Lee la URL sin dejarla en el historial del shell.

    Con terminal interactiva se pide sin eco. Sin ella (`claude !`, un script, CI)
    getpass lanzaria EOFError con un traceback feo, asi que se lee de stdin: eso
    permite `cat url.txt | upv-mcp-config set schedule` sin que el token pase por
    la linea de comandos.
    """
    if sys.stdin.isatty():
        try:
            return getpass(f"URL iCal para '{calendar}' (no se mostrara): ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    url = sys.stdin.readline().strip()
    if not url:
        print(
            "No hay terminal interactiva y stdin llego vacio.\n"
            "Ejecutalo en una terminal, o pasa la URL por stdin sin que quede en el\n"
            "historial del shell:\n"
            "    cat fichero-con-la-url.txt | upv-mcp-config set schedule",
            file=sys.stderr,
        )
    return url


def _set_poliformat() -> int:
    """Guarda usuario y contrasena de PoliformaT.

    La contrasena se pide sin eco y se guarda directamente en el llavero: no pasa
    por la linea de comandos, ni por el historial, ni se imprime nunca.
    """
    if sys.stdin.isatty():
        usuario = input("Usuario UPV (DNI o identificador): ").strip()
        try:
            clave = getpass("Contrasena (no se mostrara): ")
        except (EOFError, KeyboardInterrupt):
            clave = ""
    else:
        # Sin TTY: dos lineas por stdin, usuario primero.
        usuario = sys.stdin.readline().strip()
        clave = sys.stdin.readline().strip()

    if not usuario or not clave:
        print(
            "Cancelado: hacen falta usuario y contrasena.\n"
            "Sin terminal interactiva, pasalos por stdin en dos lineas.",
            file=sys.stderr,
        )
        return 1

    write_secret(POLIFORMAT_USER_KEY, usuario)
    write_secret(POLIFORMAT_PASSWORD_KEY, clave)
    print(f"Credenciales de PoliformaT guardadas en el llavero ({KEYRING_SERVICE}).")
    _quitar_bloqueo_cas()
    return 0


def _quitar_bloqueo_cas() -> None:
    """Credenciales nuevas: se vuelve a permitir el login que CAS habia rechazado."""
    try:
        ruta = load_settings().cas_lock_path
    except ValueError:
        return  # Sin origen configurado no ha podido haber ningun login.
    if ruta.exists():
        ruta.unlink()
        print("Desbloqueado el login en PoliformaT: se volvera a intentar con estas.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="upv-mcp-config",
        description="Guarda las credenciales de upv-mcp en el llavero del sistema.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    objetivos = sorted([*_CALENDARS, _POLIFORMAT])

    p_set = sub.add_parser("set", help="Guardar una credencial (se pide sin eco).")
    p_set.add_argument("calendar", choices=objetivos)

    p_show = sub.add_parser("show", help="Ver que esta configurado (enmascarado).")
    p_show.add_argument("calendar", choices=objetivos, nargs="?")

    p_del = sub.add_parser("delete", help="Borrar una credencial del llavero.")
    p_del.add_argument("calendar", choices=objetivos)

    args = parser.parse_args(argv)

    if args.command == "set" and args.calendar == _POLIFORMAT:
        return _set_poliformat()

    if args.command == "show":
        names = [args.calendar] if args.calendar else objetivos
        for name in names:
            if name == _POLIFORMAT:
                usuario = read_secret(POLIFORMAT_USER_KEY)
                clave = read_secret(POLIFORMAT_PASSWORD_KEY)
                estado = (
                    f"usuario {usuario}, contrasena guardada"
                    if usuario and clave
                    else "(sin configurar)"
                )
                print(f"{name:10} {estado}")
                continue
            value = read_secret(_CALENDARS[name])
            print(f"{name:10} {_mask(value) if value else '(sin configurar)'}")
        return 0

    if args.command == "delete" and args.calendar == _POLIFORMAT:
        delete_secret(POLIFORMAT_USER_KEY)
        delete_secret(POLIFORMAT_PASSWORD_KEY)
        print("Borradas las credenciales de PoliformaT del llavero.")
        return 0

    if args.command == "set":
        key = _CALENDARS[args.calendar]
        url = _read_url(args.calendar)
        if not url:
            print("Cancelado: no se recibio ninguna URL.", file=sys.stderr)
            return 1
        if not url.startswith(("http://", "https://", "webcal://")):
            print("Cancelado: no parece una URL.", file=sys.stderr)
            return 1
        write_secret(key, url)
        print(f"Guardado en el llavero ({KEYRING_SERVICE} / {key}).")
        return 0

    if args.command == "delete":
        delete_secret(_CALENDARS[args.calendar])
        print(f"Borrado '{args.calendar}' del llavero.")
        return 0

    return 1  # pragma: no cover - argparse ya exige un subcomando


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
