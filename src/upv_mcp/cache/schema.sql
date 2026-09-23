-- Esquema de la cache local. Migraciones versionadas con PRAGMA user_version.
--
-- Las fechas se guardan en UTC ISO-8601 para que el orden lexicografico coincida
-- con el cronologico y los BETWEEN de las consultas sean correctos. La conversion
-- a Europe/Madrid ocurre al leer, nunca aqui.

-- migration:1
CREATE TABLE IF NOT EXISTS sessions (
    uid            TEXT PRIMARY KEY,
    calendar       TEXT NOT NULL,
    kind           TEXT NOT NULL,
    course_code    TEXT NOT NULL,
    course_name    TEXT NOT NULL,
    course_acronym TEXT,
    start_utc      TEXT NOT NULL,
    end_utc        TEXT NOT NULL,
    room           TEXT,
    building       TEXT,
    location_raw   TEXT,
    teacher        TEXT,
    teaching_type  TEXT,
    groups_json    TEXT NOT NULL DEFAULT '[]',
    source         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions (start_utc);
CREATE INDEX IF NOT EXISTS idx_sessions_kind_start ON sessions (kind, start_utc);

CREATE TABLE IF NOT EXISTS assignments (
    uid            TEXT PRIMARY KEY,
    calendar       TEXT NOT NULL,
    kind           TEXT NOT NULL,
    title          TEXT NOT NULL,
    course_code    TEXT NOT NULL,
    course_name    TEXT NOT NULL,
    course_acronym TEXT,
    due_utc        TEXT NOT NULL,
    room           TEXT,
    building       TEXT,
    location_raw   TEXT,
    url            TEXT,
    source         TEXT NOT NULL,
    -- migration:3  estado de entrega (solo lo sabe PoliformaT)
    submission_status TEXT,
    submitted_at      TEXT,
    submitted_late    INTEGER,
    graded            INTEGER,
    grade             TEXT,
    grade_max         TEXT,
    feedback          TEXT
);

CREATE INDEX IF NOT EXISTS idx_assignments_due ON assignments (due_utc);

-- Estado de frescura por calendario: permite servir cache marcada como stale
-- cuando la red falla, en vez de dejar al usuario sin respuesta.
CREATE TABLE IF NOT EXISTS calendar_meta (
    calendar      TEXT PRIMARY KEY,
    fetched_at    TEXT NOT NULL,
    etag          TEXT,
    last_modified TEXT
);

-- migration:2  (v1: PoliformaT)
-- Materiales: solo metadatos y URL. El contenido de los ficheros no se descarga
-- nunca; una sola asignatura tiene 37 PDFs.
CREATE TABLE IF NOT EXISTS materials (
    url            TEXT PRIMARY KEY,
    calendar       TEXT NOT NULL,
    course_code    TEXT NOT NULL,
    course_name    TEXT NOT NULL,
    course_acronym TEXT,
    title          TEXT NOT NULL,
    content_type   TEXT,
    updated_at     TEXT,
    size_bytes     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_materials_course ON materials (course_code);

CREATE TABLE IF NOT EXISTS announcements (
    uid            TEXT PRIMARY KEY,
    calendar       TEXT NOT NULL,
    course_code    TEXT NOT NULL,
    course_name    TEXT NOT NULL,
    course_acronym TEXT,
    title          TEXT NOT NULL,
    body           TEXT NOT NULL,
    author         TEXT,
    published_at   TEXT NOT NULL,
    url            TEXT
);

CREATE INDEX IF NOT EXISTS idx_announcements_published ON announcements (published_at);

-- migration:4  asignaturas de PoliformaT y su sitio de Sakai. Sin esto habria que
-- descargar los materiales de TODAS las asignaturas para poder listar los de una.
CREATE TABLE IF NOT EXISTS course_sites (
    course_code    TEXT PRIMARY KEY,
    site_id        TEXT NOT NULL,
    course_name    TEXT NOT NULL,
    course_acronym TEXT,
    calendar       TEXT NOT NULL
);

-- migration:5  estado de cada evento del .ics de entregas publicado (export_ics.py).
-- Guarda el SEQUENCE que ya han visto los clientes suscritos: un iPhone ignora un
-- evento con SEQUENCE menor que el que tiene cacheado. Las fechas van en ISO 8601
-- CON offset; una fecha sin zona aqui es un bug y se rechaza al leer.
CREATE TABLE IF NOT EXISTS ics_event_state (
    uid           TEXT PRIMARY KEY,
    due           TEXT NOT NULL,
    summary       TEXT NOT NULL,
    sequence      INTEGER NOT NULL,
    last_modified TEXT NOT NULL,
    retired_at    TEXT
);

-- migration:6  ultima subida del feed (upv-publish): si el contenido no cambia, no
-- se vuelve a subir.
CREATE TABLE IF NOT EXISTS ics_feed_upload (
    feed        TEXT PRIMARY KEY,
    sha256      TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);
