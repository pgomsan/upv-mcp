"""Origen .ics: el export iCal del calendario de la UPV.

Formato observado en el generador real (ASIC iCal Generator 1.0), verificado sobre
un fichero de 841 eventos:

    SUMMARY:EST                                  <- siglas, no el nombre
    LOCATION:AULA 1E 0.2 (Edificio 1E)
    DTSTART:20240912T130000Z                     <- UTC real, con DST correcto
    DESCRIPTION:<b>Estadistica (14530)</b><br/>Tipo de docencia:  TA/TS<br/>
                Docente  :Martinez Gomez, Monica<br>Grupo POD:  TA-2A (Castellano)

Detalles que importan y que costaria descubrir de nuevo:

* El nombre de la asignatura NO esta en SUMMARY, sino en el <b> de DESCRIPTION.
* DESCRIPTION lleva HTML crudo con <br/> y <br> mezclados, y dobles espacios tras
  los dos puntos.
* Los DTSTART vienen en UTC y el generador SI ajusta el cambio de hora (una misma
  clase salta de 13:00Z a 14:00Z al pasar a horario de invierno). Basta convertir.
* El export viene pre-expandido: no hay RRULE ni VTIMEZONE que resolver.
* Este calendario es "Horario de Clases" y no contiene examenes. Si se configura un
  segundo calendario de examenes, se clasifica por contenido (ver `_classify`).
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import httpx2
from icalendar import Calendar

from upv_mcp.config import USER_AGENT, CalendarSpec, Settings
from upv_mcp.models import (
    Assignment,
    ClassSession,
    Course,
    EventKind,
    Location,
    SourceName,
)
from upv_mcp.sources.base import (
    RateLimiter,
    RetryableError,
    SourceError,
    SourcePayload,
    with_retry,
)

#: Separadores de linea que usa el generador, en sus dos variantes.
_BR: Final = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAGS: Final = re.compile(r"<[^>]+>")

#: "Estadistica (14530)" -> nombre + codigo.
_COURSE: Final = re.compile(r"^(?P<name>.+?)\s*\((?P<code>\d{4,6})\)\s*$")

#: "AULA 1E 0.2 (Edificio 1E)" -> el edificio es el ultimo parentesis si dice "Edificio".
_BUILDING: Final = re.compile(r"\((?P<building>Edificio[^)]*)\)\s*$", re.IGNORECASE)

#: Etiquetas del cuerpo de DESCRIPTION.
_LABELS: Final = {
    "tipo de docencia": "teaching_type",
    "docente": "teacher",
    "grupo pod": "groups",
}

#: Palabras que delatan un examen cuando el calendario no lo marca de otra forma.
_EXAM_HINTS: Final = re.compile(
    r"\b(examen|exame|parcial|final|convocatoria|prueba escrita)\b", re.IGNORECASE
)


def _strip_html(raw: str) -> list[str]:
    """Convierte el DESCRIPTION HTML en lineas limpias."""
    text = html.unescape(raw)
    parts = _BR.split(text)
    return [html.unescape(_TAGS.sub("", p)).strip() for p in parts]


def _parse_course(line: str, acronym: str | None) -> Course:
    """Extrae asignatura y codigo de la primera linea del DESCRIPTION."""
    cleaned = line.strip()
    if match := _COURSE.match(cleaned):
        return Course(
            code=match.group("code"),
            name=match.group("name").strip(),
            acronym=acronym,
        )
    # Sin codigo reconocible seguimos adelante: es preferible una asignatura sin
    # codigo a perder el evento entero.
    return Course(code="", name=cleaned or (acronym or "Desconocida"), acronym=acronym)


def _parse_location(raw: str | None) -> Location | None:
    """Separa aula y edificio. Hay valores vacios y con espacios sobrantes."""
    if raw is None:
        return None
    cleaned = " ".join(raw.split())
    if not cleaned:
        return None
    if match := _BUILDING.search(cleaned):
        building = match.group("building").strip()
        room = cleaned[: match.start()].strip() or None
        return Location(room=room, building=building, raw=raw)
    return Location(room=cleaned, building=None, raw=raw)


def _parse_groups(value: str) -> list[str]:
    """'TA-2A (Castellano),TS-2A (Castellano)' -> ['TA-2A', 'TS-2A'] sin duplicados."""
    groups: list[str] = []
    for chunk in value.split(","):
        name = chunk.split("(")[0].strip()
        if name and name not in groups:
            groups.append(name)
    return groups


def _parse_description(raw: str, acronym: str | None) -> tuple[Course, dict[str, str]]:
    lines = [line for line in _strip_html(raw) if line]
    if not lines:
        return Course(code="", name=acronym or "Desconocida", acronym=acronym), {}

    course = _parse_course(lines[0], acronym)
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        key = _LABELS.get(" ".join(label.split()).lower())
        if key:
            fields[key] = " ".join(value.split())
    return course, fields


def _classify(course_name: str, summary: str, description: str, calendar_name: str) -> EventKind:
    """Decide si un evento es clase o examen.

    El calendario de horarios trae siempre 'Tipo de docencia'; el de examenes no
    existe todavia, asi que la deteccion es por palabras clave y por el calendario
    de procedencia. Al aislar la decision aqui, incorporar el feed de examenes
    consiste en ajustar esta funcion.
    """
    if "tipo de docencia" in description.lower():
        return EventKind.CLASS
    if calendar_name == "exams":
        return EventKind.EXAM
    if _EXAM_HINTS.search(f"{summary} {course_name} {description}"):
        return EventKind.EXAM
    return EventKind.CLASS


def _as_datetime(value: Any, tz: ZoneInfo) -> datetime | None:  # noqa: ANN401 - icalendar no tipa
    """Normaliza a datetime aware en la zona pedida.

    Un VEVENT de dia completo llega como `date`; se ancla a medianoche local.
    """
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(tz)
    if hasattr(value, "year") and hasattr(value, "month"):
        return datetime(value.year, value.month, value.day, tzinfo=tz)
    return None


class IcsSource:
    """Lee uno o varios calendarios .ics y los convierte en modelos de dominio."""

    def __init__(self, settings: Settings, calendars: list[CalendarSpec] | None = None) -> None:
        self._settings = settings
        self._calendars = calendars if calendars is not None else settings.calendars
        self._tz = ZoneInfo(settings.timezone)
        self._limiter = RateLimiter(
            rate_per_second=1.0 / max(settings.http_min_interval_seconds, 0.001)
        )

    @property
    def name(self) -> str:
        return SourceName.ICS.value

    # -- Obtencion ----------------------------------------------------------------

    async def _download(self, url: str) -> str:
        """Descarga con timeout explicito; traduce fallos transitorios a RetryableError."""
        await self._limiter.acquire()
        timeout = self._settings.http_timeout_seconds
        try:
            async with httpx2.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                response = await client.get(url)
        except httpx2.TimeoutException as exc:
            raise RetryableError(f"Timeout tras {timeout}s descargando el calendario") from exc
        except httpx2.RequestError as exc:
            raise RetryableError(f"Error de red descargando el calendario: {exc}") from exc

        if response.status_code == 429 or response.status_code >= 500:
            retry_after = response.headers.get("Retry-After")
            raise RetryableError(
                f"El servidor respondio {response.status_code}",
                retry_after_seconds=float(retry_after) if retry_after and
                retry_after.isdigit() else None,
            )
        if response.status_code >= 400:
            # 401/403/404 no se arreglan reintentando: casi siempre es el token.
            raise SourceError(
                f"El servidor respondio {response.status_code}. Revisa que tu URL iCal "
                "siga siendo valida (regenera el enlace en la intranet si hace falta)."
            )
        return response.text

    async def read_calendar(self, spec: CalendarSpec) -> SourcePayload:
        """Obtiene y parsea un unico calendario. Lo usa el repositorio al refrescar."""
        return self.parse(await self._read(spec), spec.name)

    async def _read(self, spec: CalendarSpec) -> str:
        if spec.path is not None:
            try:
                return spec.path.read_text(encoding="utf-8")
            except OSError as exc:
                raise SourceError(f"No se pudo leer {spec.path}: {exc}") from exc
        if spec.url is None:  # pragma: no cover - Settings ya lo garantiza
            raise SourceError(f"El calendario '{spec.name}' no tiene ni url ni fichero.")
        return await with_retry(
            lambda: self._download(spec.url or ""),
            attempts=self._settings.http_max_attempts,
            base_delay=self._settings.http_backoff_base_seconds,
        )

    # -- Parseo -------------------------------------------------------------------

    def parse(self, raw_ics: str, calendar_name: str = "schedule") -> SourcePayload:
        """Convierte texto iCal en modelos. Es puro: no toca red ni disco."""
        try:
            calendar = Calendar.from_ical(raw_ics)
        except Exception as exc:
            raise SourceError(f"El calendario '{calendar_name}' no es iCal valido: {exc}") from exc

        sessions: list[ClassSession] = []
        assignments: list[Assignment] = []

        for component in calendar.walk("VEVENT"):
            session = self._build_session(component, calendar_name)
            if session is None:
                continue
            sessions.append(session)
            if session.kind is EventKind.EXAM:
                assignments.append(
                    Assignment(
                        uid=session.uid,
                        kind=EventKind.EXAM,
                        title=f"Examen de {session.course.name}",
                        course=session.course,
                        due=session.start,
                        location=session.location,
                        source=SourceName.ICS,
                    )
                )

        sessions.sort(key=lambda s: s.start)
        assignments.sort(key=lambda a: a.due)
        return SourcePayload(
            sessions=sessions,
            assignments=assignments,
            fetched_at=datetime.now(self._tz),
        )

    def _build_session(
        self,
        component: Any,  # noqa: ANN401 - componente de icalendar, sin tipar
        calendar_name: str,
    ) -> ClassSession | None:
        """Construye una sesion. Devuelve None si al evento le falta lo imprescindible.

        Se omite en silencio en vez de fallar: un evento roto no debe tumbar la
        respuesta entera de una tool.
        """
        start = _as_datetime(getattr(component.get("DTSTART"), "dt", None), self._tz)
        end = _as_datetime(getattr(component.get("DTEND"), "dt", None), self._tz)
        if start is None:
            return None
        if end is None:
            end = start

        summary = str(component.get("SUMMARY") or "").strip() or None
        description = str(component.get("DESCRIPTION") or "")
        location_raw = component.get("LOCATION")
        uid = str(component.get("UID") or "").strip()
        if not uid:
            uid = f"{summary or 'evento'}@{start.isoformat()}"

        course, fields = _parse_description(description, summary)
        kind = _classify(course.name, summary or "", description, calendar_name)

        return ClassSession(
            uid=uid,
            kind=kind,
            course=course,
            start=start,
            end=end,
            location=_parse_location(str(location_raw) if location_raw is not None else None),
            teacher=fields.get("teacher") or None,
            teaching_type=fields.get("teaching_type") or None,
            groups=_parse_groups(fields.get("groups", "")),
            source=SourceName.ICS,
        )

    # -- Contrato AcademicSource --------------------------------------------------

    async def fetch(self, *, force_refresh: bool = False) -> SourcePayload:
        """Descarga y parsea todos los calendarios configurados."""
        sessions: list[ClassSession] = []
        assignments: list[Assignment] = []
        for spec in self._calendars:
            payload = self.parse(await self._read(spec), spec.name)
            sessions.extend(payload.sessions)
            assignments.extend(payload.assignments)

        # Un mismo evento puede aparecer en dos calendarios; el UID manda.
        seen: set[str] = set()
        unique: list[ClassSession] = []
        for session in sessions:
            if session.uid in seen:
                continue
            seen.add(session.uid)
            unique.append(session)
        unique.sort(key=lambda s: s.start)
        assignments.sort(key=lambda a: a.due)
        return SourcePayload(
            sessions=unique,
            assignments=assignments,
            fetched_at=datetime.now(self._tz),
        )


def source_from_file(path: Path, settings: Settings, name: str = "schedule") -> IcsSource:
    """Atajo para tests y uso sin red."""
    return IcsSource(settings, [CalendarSpec(name=name, path=path)])
