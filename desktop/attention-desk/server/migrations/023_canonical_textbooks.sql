-- Canonical Eric OS library references. Original bytes remain in that library.
CREATE TABLE textbook_original (
    attachment_id TEXT PRIMARY KEY REFERENCES attachment(id),
    canonical_path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL UNIQUE,
    bytes INTEGER NOT NULL CHECK(bytes>0),
    mtime_ns INTEGER NOT NULL,
    page_count INTEGER NOT NULL CHECK(page_count BETWEEN 1 AND 2000),
    manifest_sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    registered_at TEXT NOT NULL
);
CREATE TRIGGER textbook_original_immutable BEFORE UPDATE ON textbook_original
BEGIN SELECT RAISE(ABORT,'Canonical textbook registration is immutable'); END;
CREATE TRIGGER textbook_original_preserved BEFORE DELETE ON textbook_original
BEGIN SELECT RAISE(ABORT,'Canonical textbook provenance must be preserved'); END;
ALTER TABLE textbook ADD COLUMN acceptance_status TEXT NOT NULL DEFAULT 'pending_evaluation';
ALTER TABLE textbook ADD COLUMN acceptance_json TEXT;
ALTER TABLE textbook ADD COLUMN accepted_at TEXT;
INSERT INTO textbook(id,course_id,title,edition,editor,publisher,role,file_role,created_at,updated_at)
VALUES ('textbook-huang-v2-lower','course-math-ii','高等数学（第二版）（下）','第二版·下册','黄立宏','北京大学出版社','companion','full_textbook',strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now'));
