-- Remove the Math II pilot restriction without changing existing identities.
-- PersonalStore runs migrations with FK checks deferred to the final audit.
-- Rebuild parents under their original names so every child FK stays intact.
CREATE TABLE textbook_next (
    id TEXT PRIMARY KEY,
    document_id TEXT REFERENCES document(id),
    course_id TEXT NOT NULL REFERENCES course(id),
    title TEXT NOT NULL,
    edition TEXT,
    editor TEXT,
    publisher TEXT,
    isbn TEXT,
    role TEXT NOT NULL DEFAULT 'primary',
    file_role TEXT NOT NULL DEFAULT 'full_textbook',
    status TEXT NOT NULL DEFAULT 'waiting_file',
    is_fixture INTEGER NOT NULL DEFAULT 0 CHECK(is_fixture IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    acceptance_status TEXT NOT NULL DEFAULT 'pending_evaluation',
    acceptance_json TEXT,
    accepted_at TEXT
);
INSERT INTO textbook_next SELECT * FROM textbook;
DROP TABLE textbook;
ALTER TABLE textbook_next RENAME TO textbook;
CREATE INDEX textbook_course ON textbook(course_id,role,id);

CREATE TABLE learning_unit_next (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES course(id),
    title TEXT NOT NULL,
    sequence_number INTEGER,
    status TEXT NOT NULL DEFAULT 'provisional',
    created_at TEXT NOT NULL
);
INSERT INTO learning_unit_next SELECT * FROM learning_unit;
DROP TABLE learning_unit;
ALTER TABLE learning_unit_next RENAME TO learning_unit;
CREATE UNIQUE INDEX learning_unit_course ON learning_unit(id,course_id);

CREATE TABLE lecture_next (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES course(id),
    lecture_date TEXT,
    title TEXT NOT NULL,
    sequence_number INTEGER,
    learning_unit_id TEXT REFERENCES learning_unit(id),
    status TEXT NOT NULL DEFAULT 'needs_sources',
    accepted_artifact_id TEXT REFERENCES review_artifact(id),
    proposal_snapshot_id TEXT REFERENCES raw_snapshot(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(learning_unit_id,course_id) REFERENCES learning_unit(id,course_id)
);
INSERT INTO lecture_next SELECT * FROM lecture;
DROP TABLE lecture;
ALTER TABLE lecture_next RENAME TO lecture;
CREATE INDEX lecture_course_date ON lecture(course_id,lecture_date DESC,created_at DESC);

-- Course ownership is stable. Moving a note uses the explicit note-link owner;
-- it must not reassign a class, book or unit and reinterpret old evidence.
CREATE TRIGGER textbook_course_stable BEFORE UPDATE OF course_id ON textbook
WHEN NEW.course_id IS NOT OLD.course_id
BEGIN SELECT RAISE(ABORT,'Textbook course identity is immutable'); END;
CREATE TRIGGER learning_unit_course_stable BEFORE UPDATE OF course_id ON learning_unit
WHEN NEW.course_id IS NOT OLD.course_id
BEGIN SELECT RAISE(ABORT,'Learning unit course identity is immutable'); END;
CREATE TRIGGER lecture_course_stable BEFORE UPDATE OF course_id ON lecture
WHEN NEW.course_id IS NOT OLD.course_id
BEGIN SELECT RAISE(ABORT,'Lecture course identity is immutable'); END;

CREATE TRIGGER section_unit_course_insert BEFORE INSERT ON document_section
WHEN NEW.learning_unit_id IS NOT NULL AND
    (SELECT course_id FROM learning_unit WHERE id=NEW.learning_unit_id)
    IS NOT (SELECT course_id FROM textbook WHERE id=NEW.textbook_id)
BEGIN SELECT RAISE(ABORT,'Section unit belongs to another course'); END;
CREATE TRIGGER section_course_stable BEFORE UPDATE OF textbook_id ON document_section
WHEN (SELECT course_id FROM textbook WHERE id=OLD.textbook_id) IS NOT
    (SELECT course_id FROM textbook WHERE id=NEW.textbook_id)
BEGIN SELECT RAISE(ABORT,'Section course identity is immutable'); END;
CREATE TRIGGER section_unit_course_update BEFORE UPDATE OF learning_unit_id,textbook_id ON document_section
WHEN NEW.learning_unit_id IS NOT NULL AND
    (SELECT course_id FROM learning_unit WHERE id=NEW.learning_unit_id)
    IS NOT (SELECT course_id FROM textbook WHERE id=NEW.textbook_id)
BEGIN SELECT RAISE(ABORT,'Section unit belongs to another course'); END;
CREATE TRIGGER lecture_section_course_insert BEFORE INSERT ON lecture_section
WHEN (SELECT course_id FROM lecture WHERE id=NEW.lecture_id) IS NOT
    (SELECT t.course_id FROM document_section s JOIN textbook t ON t.id=s.textbook_id WHERE s.id=NEW.section_id)
BEGIN SELECT RAISE(ABORT,'Section belongs to another course'); END;
CREATE TRIGGER lecture_section_course_update BEFORE UPDATE OF lecture_id,section_id ON lecture_section
WHEN (SELECT course_id FROM lecture WHERE id=NEW.lecture_id) IS NOT
    (SELECT t.course_id FROM document_section s JOIN textbook t ON t.id=s.textbook_id WHERE s.id=NEW.section_id)
BEGIN SELECT RAISE(ABORT,'Section belongs to another course'); END;
