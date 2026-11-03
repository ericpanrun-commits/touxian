-- Learning evidence is local, append-only, and separate from formal Tasks.
CREATE TABLE learning_topic (
 id TEXT PRIMARY KEY,
 course_id TEXT NOT NULL REFERENCES course(id),
 section_id TEXT UNIQUE REFERENCES document_section(id),
 title TEXT NOT NULL,
 history_status TEXT NOT NULL DEFAULT 'none' CHECK(history_status IN ('none','suggested','confirmed','dismissed')),
 exposure_json TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE learning_capture (
 id TEXT PRIMARY KEY,
 raw_item_id TEXT NOT NULL REFERENCES raw_item(id),
 snapshot_id TEXT NOT NULL REFERENCES raw_snapshot(id),
 input_hash TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL,
 context_json TEXT NOT NULL,
 parsed_json TEXT,
 error_json TEXT,
 created_at TEXT NOT NULL,
 UNIQUE(raw_item_id,snapshot_id)
);
CREATE TABLE learning_artifact (
 id TEXT PRIMARY KEY,
 kind TEXT NOT NULL,
 topic_id TEXT REFERENCES learning_topic(id),
 input_hash TEXT NOT NULL,
 status TEXT NOT NULL,
 payload_json TEXT,
 source_json TEXT NOT NULL DEFAULT '[]',
 model TEXT,
 usage_json TEXT,
 cost_json TEXT,
 error_json TEXT,
 created_at TEXT NOT NULL,
 completed_at TEXT,
 UNIQUE(kind,input_hash)
);
CREATE TABLE learning_exercise (
 id TEXT PRIMARY KEY,
 course_id TEXT NOT NULL REFERENCES course(id),
 topic_id TEXT REFERENCES learning_topic(id),
 lecture_id TEXT REFERENCES lecture(id),
 capture_id TEXT REFERENCES learning_capture(id),
 artifact_id TEXT REFERENCES learning_artifact(id),
 kind TEXT NOT NULL CHECK(kind IN ('classroom','diagnostic','self_test','retest','exam')),
 fingerprint TEXT NOT NULL UNIQUE,
 question TEXT NOT NULL,
 options_json TEXT NOT NULL DEFAULT '{}',
 answer_json TEXT,
 explanation TEXT,
 sources_json TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL,
 question_order INTEGER NOT NULL DEFAULT 0 CHECK(question_order>=0)
);
CREATE TABLE learning_attempt (
 id TEXT PRIMARY KEY,
 exercise_id TEXT NOT NULL REFERENCES learning_exercise(id),
 capture_id TEXT REFERENCES learning_capture(id),
 answer TEXT NOT NULL,
 result TEXT NOT NULL CHECK(result IN ('correct','incorrect','unknown')),
 result_origin TEXT NOT NULL,
 independent INTEGER NOT NULL DEFAULT 0 CHECK(independent IN (0,1)),
 occurred_at TEXT,
 received_at TEXT NOT NULL,
 fingerprint TEXT NOT NULL UNIQUE,
 mistake_json TEXT NOT NULL DEFAULT '{}',
 retest_of TEXT REFERENCES learning_attempt(id),
 retest_success INTEGER NOT NULL DEFAULT 0 CHECK(retest_success IN (0,1))
 ,supersedes_id TEXT REFERENCES learning_attempt(id)
);
CREATE INDEX learning_attempt_exercise ON learning_attempt(exercise_id,received_at);
CREATE TABLE learning_followup (
 id TEXT PRIMARY KEY,
 attempt_id TEXT NOT NULL UNIQUE REFERENCES learning_attempt(id),
 exercise_id TEXT REFERENCES learning_exercise(id),
 due_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','ready','resolved','needs_source')),
 resolved_attempt_id TEXT REFERENCES learning_attempt(id),
 created_at TEXT NOT NULL
);
CREATE INDEX learning_followup_due ON learning_followup(status,due_at);
CREATE TRIGGER learning_attempt_immutable BEFORE UPDATE ON learning_attempt
BEGIN SELECT RAISE(ABORT,'Learning attempts are immutable'); END;
CREATE TRIGGER learning_attempt_preserved BEFORE DELETE ON learning_attempt
BEGIN SELECT RAISE(ABORT,'Learning attempts must be preserved'); END;
CREATE TRIGGER learning_exercise_immutable BEFORE UPDATE ON learning_exercise
BEGIN SELECT RAISE(ABORT,'Learning exercises are immutable'); END;
CREATE TRIGGER learning_exercise_preserved BEFORE DELETE ON learning_exercise
BEGIN SELECT RAISE(ABORT,'Learning exercises must be preserved'); END;
