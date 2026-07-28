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
    SCHEDULE_KEY,
    delete_secret,
    read_secret,
    write_secret,
)

_CALENDARS = {"schedule": SCHEDULE_KEY, "exams": EXAMS_KEY}


def _mask(value: str) -> str:
    """Muestra lo justo para reconocer la URL sin revelar el token."""
    if len(value) <= 24:
        return value[:8] + "..."
    return f"{value[:32]}...{value[-4:]} ({len(value)} chars)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="upv-mcp-config",
        description="Guarda las URL iCal de la UPV en el llavero del sistema.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_set = sub.add_parser("set", help="Guardar una URL iCal (se pide sin eco por pantalla).")
    p_set.add_argument("calendar", choices=sorted(_CALENDARS))

    p_show = sub.add_parser("show", help="Ver que calendarios estan configurados (enmascarado).")
    p_show.add_argument("calendar", choices=sorted(_CALENDARS), nargs="?")

    p_del = sub.add_parser("delete", help="Borrar una URL del llavero.")
    p_del.add_argument("calendar", choices=sorted(_CALENDARS))

    args = parser.parse_args(argv)

    if args.command == "set":
        key = _CALENDARS[args.calendar]
        url = getpass(f"URL iCal para '{args.calendar}' (no se mostrara): ").strip()
        if not url:
            print("Cancelado: URL vacia.", file=sys.stderr)
            return 1
        if not url.startswith(("http://", "https://", "webcal://")):
            print("Cancelado: no parece una URL.", file=sys.stderr)
            return 1
        write_secret(key, url)
        print(f"Guardado en el llavero ({KEYRING_SERVICE} / {key}).")
        return 0

    if args.command == "show":
        names = [args.calendar] if args.calendar else sorted(_CALENDARS)
        for name in names:
            value = read_secret(_CALENDARS[name])
            print(f"{name:10} {_mask(value) if value else '(sin configurar)'}")
        return 0

    if args.command == "delete":
        delete_secret(_CALENDARS[args.calendar])
        print(f"Borrado '{args.calendar}' del llavero.")
        return 0

    return 1  # pragma: no cover - argparse ya exige un subcomando


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
