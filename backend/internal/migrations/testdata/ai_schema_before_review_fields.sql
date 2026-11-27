-- Postgres (NeonDB) counterpart of local_dev_schema.sql. Same tables, same
-- FKs, same column names, so the two never drift into being different data
-- models -- only the two syntax spots SQLite and Postgres actually disagree
-- on changed: BLOB -> BYTEA, and AUTOINCREMENT -> GENERATED ALWAYS AS
-- IDENTITY. Everything else (TEXT, FOREIGN KEY, ON CONFLICT ... DO UPDATE)
-- is valid in both engines already.
CREATE TABLE IF NOT EXISTS stores (
    store_id TEXT PRIMARY KEY,
    store_code TEXT,
    display_name TEXT NOT NULL,
    timezone TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS cameras (
    camera_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    camera_code TEXT,
    display_name TEXT NOT NULL,
    camera_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (store_id) REFERENCES stores(store_id)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    stopped_at TEXT,
    status TEXT NOT NULL,
    source_ref TEXT,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (camera_id) REFERENCES cameras(camera_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    run_id TEXT,
    pipeline_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    identity_person_id TEXT,
    identity_label TEXT,
    identity_score REAL,
    reason_codes_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (store_id) REFERENCES stores(store_id),
    FOREIGN KEY (camera_id) REFERENCES cameras(camera_id)
);

CREATE TABLE IF NOT EXISTS alert_media (
    media_id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    mime_type TEXT,
    started_at TEXT,
    ended_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (alert_id) REFERENCES alerts(alert_id)
);

CREATE TABLE IF NOT EXISTS persons (
    person_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    is_staff INTEGER NOT NULL DEFAULT 1,
    company_id TEXT,
    store_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (store_id) REFERENCES stores(store_id)
);

CREATE TABLE IF NOT EXISTS face_embeddings (
    embedding_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    embedding BYTEA NOT NULL,
    detector_score REAL,
    image_path TEXT,
    source TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_face_embeddings_person
    ON face_embeddings(person_id);

CREATE INDEX IF NOT EXISTS idx_persons_store
    ON persons(store_id);

CREATE INDEX IF NOT EXISTS idx_persons_company
    ON persons(company_id);

CREATE TABLE IF NOT EXISTS owners (
    owner_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT,
    telegram_chat_id TEXT,
    password_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_stores (
    owner_id TEXT NOT NULL REFERENCES owners(owner_id) ON DELETE CASCADE,
    store_id TEXT NOT NULL REFERENCES stores(store_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, store_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_stores_store
    ON owner_stores(store_id);
