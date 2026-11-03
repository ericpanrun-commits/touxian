-- The command receipt and its guarded domain changes commit in the same SQLite transaction.
CREATE TABLE cloud_command_receipt (
    id TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
    result_json TEXT NOT NULL CHECK(json_valid(result_json)),
    created_at TEXT NOT NULL
);
CREATE TABLE cloud_task_application (
    cloud_revision INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX cloud_task_event_versions ON cloud_task_application(event_id,from_version,to_version);
