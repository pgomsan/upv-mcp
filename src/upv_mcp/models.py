"""Modelos de dominio.

Esta capa no sabe nada de MCP ni de HTTP: es el contrato entre `sources/` (que
produce estos objetos) y `tools/` (que los filtra y los devuelve). Anadir PoliformaT
en la v1 significa producir estos mismos modelos desde otra fuente.

Todos los datetime son *aware* y estan normalizados a Europe/Madrid en el parseo.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class EventKind(StrEnum):
    """Naturaleza de un evento academico."""

    CLASS = "class"
    EXAM = "exam"
    ASSIGNMENT = "assignment"


class SourceName(StrEnum):
    """Origen del dato. Permite al modelo explicar de donde sale cada cosa."""

    ICS = "ics"
    POLIFORMAT = "poliformat"


class SubmissionStatus(StrEnum):
    """Estado de entrega de una tarea.

    `UNKNOWN` no es un adorno: hay tareas que no traen registro de entrega (6 de 82
    en datos reales). Tratarlas como no entregadas seria inventar, y podria hacer
    que el estudiante creyera que le falta algo que ya hizo.
    """

    SUBMITTED = "submitted"
    NOT_SUBMITTED = "not_submitted"
    UNKNOWN = "unknown"


class Submission(BaseModel):
    """Lo que el estudiante ha entregado en una tarea, y su correccion."""

    status: SubmissionStatus
    submitted_at: datetime | None = Field(default=None, description="Cuando se entrego, si consta.")
    late: bool | None = Field(default=None, description="True si se entrego fuera de plazo.")
    graded: bool = Field(default=False, description="True si el profesor ya la ha corregido.")
    grade: str | None = Field(
        default=None,
        description="Nota tal cual la da PoliformaT, con coma decimal (p.ej. '8,30').",
    )
    grade_max: str | None = Field(
        default=None,
        description="Nota maxima de la escala. Sin esto, un '8,30' no significa nada.",
    )
    feedback: str | None = Field(
        default=None, description="Comentario del profesor, sin HTML y recortado."
    )


class Course(BaseModel):
    """Asignatura. `code` es el codigo UPV de 5 digitos (p.ej. 14530)."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(description="Codigo UPV de la asignatura, p.ej. '14530'.")
    name: str = Field(description="Nombre completo, p.ej. 'Estadistica'.")
    acronym: str | None = Field(
        default=None, description="Siglas usadas en el horario, p.ej. 'EST'."
    )


class CourseSite(BaseModel):
    """Asignatura y el sitio de Sakai que la contiene.

    Hace falta para poder pedir los materiales de una asignatura concreta sin
    descargar los de todas: la URL es /direct/content/site/<site_id>.
    """

    course: Course
    site_id: str = Field(description="Id del sitio en Sakai, p.ej. 'GRA_14541_2025'.")


class Location(BaseModel):
    """Aula y edificio. `raw` conserva el texto original por si el parseo falla."""

    model_config = ConfigDict(frozen=True)

    room: str | None = Field(default=None, description="Aula o laboratorio.")
    building: str | None = Field(default=None, description="Edificio, p.ej. 'Edificio 1E'.")
    raw: str = Field(description="Valor LOCATION original del .ics.")


class ClassSession(BaseModel):
    """Una sesion con hora y sitio: clase o examen.

    Es lo que devuelven `get_schedule` y `get_next_class`.
    """

    uid: str = Field(description="Identificador estable del evento en el calendario.")
    kind: EventKind = Field(description="'class' o 'exam'.")
    course: Course
    start: datetime = Field(description="Inicio, hora local de Valencia (Europe/Madrid).")
    end: datetime = Field(description="Fin, hora local de Valencia (Europe/Madrid).")
    location: Location | None = None
    teacher: str | None = Field(default=None, description="Docente, si el calendario lo trae.")
    teaching_type: str | None = Field(
        default=None,
        description="Tipo de docencia UPV: TA (teoria de aula), TS (seminario), PL (practicas).",
    )
    groups: list[str] = Field(
        default_factory=list, description="Grupos POD, p.ej. ['TA-2A', 'TS-2A']."
    )
    source: SourceName = SourceName.ICS

    @property
    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


class Assignment(BaseModel):
    """Algo con fecha limite: un examen o una entrega.

    Es lo que devuelve `list_upcoming_deadlines`. En la v0 solo puede provenir de
    examenes del calendario; las entregas llegaran de PoliformaT en la v1.
    """

    uid: str
    kind: EventKind = Field(description="'exam' o 'assignment'.")
    title: str
    course: Course
    due: datetime = Field(description="Fecha/hora limite, hora local de Valencia.")
    location: Location | None = None
    url: str | None = None
    source: SourceName
    submission: Submission | None = Field(
        default=None,
        description="Estado de entrega. Null si la fuente no lo sabe (p.ej. el .ics).",
    )

    @property
    def is_pending(self) -> bool:
        """Solo es 'pendiente' lo que consta explicitamente como no entregado.

        Lo ya corregido nunca es pendiente, aunque la fuente diga lo contrario: si
        tiene nota, el trabajo existe y no queda nada que entregar. PoliformaT
        produce esa contradiccion (ver `_submission` en sources/poliformat.py) y
        aqui se corta, para que ninguna fuente pueda colar en "lo que me falta"
        algo que el estudiante ya tiene aprobado.
        """
        if self.submission is None or self.submission.graded:
            return False
        return self.submission.status is SubmissionStatus.NOT_SUBMITTED


class Material(BaseModel):
    """Recurso de una asignatura (apuntes, enunciados, enlaces).

    Se expone como MCP *resource*, no como tool: es contenido navegable que el
    cliente decide cuando leer, no una accion que ejecutar.

    Nunca se descarga el fichero: solo sus metadatos y la URL. Una sola asignatura
    tiene 37 PDFs, y volcarlos al contexto seria justo lo contrario de lo que
    persigue este servidor.
    """

    course: Course
    title: str
    url: str
    content_type: str | None = Field(
        default=None, description="MIME, p.ej. 'application/pdf'. 'collection' es una carpeta."
    )
    updated_at: datetime | None = None
    size_bytes: int | None = None

    @property
    def is_folder(self) -> bool:
        return self.content_type == "collection"


class Announcement(BaseModel):
    """Aviso publicado por un profesor en una asignatura."""

    uid: str
    course: Course
    title: str
    body: str = Field(description="Texto del aviso, ya sin HTML.")
    author: str | None = None
    published_at: datetime
    url: str | None = None


# --------------------------------------------------------------------------------------
# Envoltorios de respuesta de las tools.
#
# Llevan siempre metadatos de cobertura para que el modelo no afirme cosas falsas:
# `truncated` evita que resuma una lista recortada como si fuera completa, y
# `coverage_note` le dice explicitamente que NO esta mirando (p.ej. entregas de
# PoliformaT en la v0).
# --------------------------------------------------------------------------------------


class ResultMeta(BaseModel):
    """Metadatos comunes a toda respuesta."""

    total_matching: int = Field(description="Elementos que cumplian el filtro antes de truncar.")
    returned: int = Field(description="Elementos realmente incluidos en esta respuesta.")
    truncated: bool = Field(
        default=False,
        description="True si se recorto la lista. Si es True, dilo al usuario y sugiere "
        "acotar el rango en vez de presentar la lista como completa.",
    )
    stale: bool = Field(
        default=False,
        description="True si no se pudo refrescar el calendario y se sirvio cache antigua.",
    )
    generated_at: datetime = Field(description="Momento en que se calculo la respuesta.")
    coverage_note: str | None = Field(
        default=None,
        description="Limitaciones de esta respuesta. Si viene, tenlo en cuenta antes de "
        "afirmar que algo no existe.",
    )


class ScheduleResult(BaseModel):
    """Respuesta de `get_schedule`."""

    range_start: datetime
    range_end: datetime
    sessions: list[ClassSession]
    meta: ResultMeta


class NextClassResult(BaseModel):
    """Respuesta de `get_next_class`."""

    session: ClassSession | None = Field(
        default=None, description="Proxima sesion, o null si no queda ninguna."
    )
    starts_in_minutes: int | None = Field(
        default=None, description="Minutos desde ahora hasta el inicio."
    )
    meta: ResultMeta


class DeadlinesResult(BaseModel):
    """Respuesta de `list_upcoming_deadlines`."""

    horizon_days: int
    days_back: int = Field(
        default=0, description="Dias hacia atras incluidos. 0 = solo lo que esta por venir."
    )
    pending_only: bool = Field(
        default=False, description="True si se filtro a lo que consta como no entregado."
    )
    deadlines: list[Assignment]
    meta: ResultMeta


class AnnouncementsResult(BaseModel):
    """Respuesta de `list_announcements`."""

    announcements: list[Announcement]
    meta: ResultMeta


class MaterialContentResult(BaseModel):
    """Respuesta de `read_material`: el texto de un fichero de PoliformaT."""

    material: Material = Field(description="El fichero que se ha leido.")
    text: str = Field(description="Texto extraido del fichero.")
    truncated: bool = Field(
        default=False,
        description="True si el documento era mas largo y se recorto. Dilo al usuario.",
    )
    candidates: list[str] = Field(
        default_factory=list,
        description="Otros ficheros que encajaban con la busqueda, por si era otro.",
    )
    meta: ResultMeta
