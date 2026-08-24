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

CREATE TABLE IF NOT EXISTS pipeline_templates (
    pipeline_template_id TEXT PRIMARY KEY,
    pipeline_type TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS camera_pipeline_bindings (
    binding_id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL,
    pipeline_template_id TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    config_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (camera_id) REFERENCES cameras(camera_id),
    FOREIGN KEY (pipeline_template_id) REFERENCES pipeline_templates(pipeline_template_id)
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
    metadata_json TEXT NOT NULL DEFAULT '{}'
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
