-- Thin document metadata over existing content-addressed Attachment originals.
CREATE TABLE document (
    id TEXT PRIMARY KEY,
    attachment_id TEXT NOT NULL REFERENCES attachment(id),
    sha256 TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    page_count INTEGER CHECK(page_count BETWEEN 1 AND 2000),
    status TEXT NOT NULL DEFAULT 'received',
    error_json TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE document_page (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document(id),
    page_index INTEGER NOT NULL CHECK(page_index >= 1),
    printed_page_label TEXT,
    label_origin TEXT NOT NULL DEFAULT 'unknown',
    page_hash TEXT NOT NULL,
    render_sha256 TEXT,
    current_extraction_key TEXT,
    vision_status TEXT,
    vision_attempt_id TEXT REFERENCES school_ai_attempt(id),
    vision_input_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error_json TEXT,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(document_id,page_index),
    UNIQUE(id,document_id,page_index)
);
CREATE TABLE document_block (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document(id),
    page_id TEXT NOT NULL,
    page_index INTEGER NOT NULL,
    block_order INTEGER NOT NULL CHECK(block_order >= 0),
    text TEXT,
    raw_snapshot_id TEXT,
    raw_block_id TEXT,
    extraction_key TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    extraction_quality TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(page_id,document_id,page_index) REFERENCES document_page(id,document_id,page_index),
    FOREIGN KEY(raw_snapshot_id,raw_block_id) REFERENCES raw_source_block(raw_snapshot_id,block_id),
    CHECK((text IS NOT NULL AND length(text)>0) OR (raw_snapshot_id IS NOT NULL AND raw_block_id IS NOT NULL)),
    UNIQUE(page_id,extraction_key,block_order)
);
CREATE INDEX document_block_page ON document_block(document_id,page_index,extraction_key);
CREATE INDEX document_block_hash ON document_block(content_hash);
CREATE TRIGGER document_block_immutable BEFORE UPDATE ON document_block
BEGIN SELECT RAISE(ABORT,'Learning source blocks are immutable'); END;
CREATE TRIGGER document_block_preserved BEFORE DELETE ON document_block
BEGIN SELECT RAISE(ABORT,'Learning source blocks must be preserved'); END;

CREATE TABLE textbook (
    id TEXT PRIMARY KEY,
    document_id TEXT REFERENCES document(id),
    course_id TEXT NOT NULL REFERENCES course(id) CHECK(course_id='course-math-ii'),
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
    updated_at TEXT NOT NULL
);
CREATE TABLE learning_unit (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES course(id) CHECK(course_id='course-math-ii'),
    title TEXT NOT NULL,
    sequence_number INTEGER,
    status TEXT NOT NULL DEFAULT 'provisional',
    created_at TEXT NOT NULL
);
CREATE TABLE document_section (
    id TEXT PRIMARY KEY,
    textbook_id TEXT NOT NULL REFERENCES textbook(id),
    document_id TEXT NOT NULL REFERENCES document(id),
    learning_unit_id TEXT REFERENCES learning_unit(id),
    title TEXT NOT NULL,
    start_page INTEGER NOT NULL CHECK(start_page>=1),
    end_page INTEGER NOT NULL CHECK(end_page>=start_page),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX document_section_pages ON document_section(document_id,start_page,end_page);
CREATE TABLE lecture (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES course(id) CHECK(course_id='course-math-ii'),
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
    updated_at TEXT NOT NULL
);
CREATE INDEX lecture_course_date ON lecture(course_id,lecture_date DESC,created_at DESC);
CREATE TABLE lecture_note (
    lecture_id TEXT NOT NULL REFERENCES lecture(id),
    document_id TEXT NOT NULL REFERENCES document(id),
    raw_item_id TEXT NOT NULL REFERENCES raw_item(id),
    PRIMARY KEY(lecture_id,document_id)
);
CREATE TABLE lecture_section (
    lecture_id TEXT NOT NULL REFERENCES lecture(id),
    section_id TEXT NOT NULL REFERENCES document_section(id),
    status TEXT NOT NULL CHECK(status IN ('suggested','confirmed','removed')),
    origin TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(lecture_id,section_id)
);

-- Existing Job storage/executor; named operations school_catalog/school_index/
-- school_review. No second queue, broker, or worker server is introduced.
ALTER TABLE attachment_job ADD COLUMN school_payload_json TEXT;
ALTER TABLE attachment_job ADD COLUMN school_result_json TEXT;
ALTER TABLE attachment_job ADD COLUMN school_fingerprint TEXT;
ALTER TABLE attachment_job ADD COLUMN school_request_id TEXT;
CREATE UNIQUE INDEX school_job_fingerprint ON attachment_job(school_fingerprint) WHERE school_fingerprint IS NOT NULL;
CREATE UNIQUE INDEX school_job_request ON attachment_job(school_request_id) WHERE school_request_id IS NOT NULL;
CREATE TABLE school_request (
    id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE school_ai_attempt (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES attachment_job(id),
    lecture_id TEXT REFERENCES lecture(id),
    kind TEXT NOT NULL CHECK(kind IN ('vision','text')),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    image_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    usage_json TEXT,
    cost_json TEXT,
    raw_output TEXT,
    error_json TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX school_attempt_input ON school_ai_attempt(kind,input_hash,status);
CREATE TABLE review_artifact (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL DEFAULT 'lecture_review' CHECK(type='lecture_review'),
    lecture_id TEXT NOT NULL REFERENCES lecture(id),
    version INTEGER NOT NULL,
    content_json TEXT,
    generated_at TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_versions_json TEXT NOT NULL,
    source_evidence_json TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    cost_json TEXT,
    error_json TEXT,
    job_id TEXT REFERENCES attachment_job(id),
    created_at TEXT NOT NULL,
    UNIQUE(lecture_id,version)
);
CREATE INDEX review_artifact_input ON review_artifact(lecture_id,input_hash,created_at DESC);
CREATE TRIGGER review_content_immutable BEFORE UPDATE OF content_json,input_versions_json,source_evidence_json,model,prompt_version ON review_artifact
WHEN OLD.content_json IS NOT NULL
BEGIN SELECT RAISE(ABORT,'Generated ReviewArtifact versions are immutable'); END;
CREATE TRIGGER review_artifact_preserved BEFORE DELETE ON review_artifact
BEGIN SELECT RAISE(ABORT,'ReviewArtifact versions must be preserved'); END;
CREATE TABLE review_feedback (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES review_artifact(id),
    action TEXT NOT NULL CHECK(action IN ('accept','reject','comment')),
    feedback TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- User-confirmed metadata only. No PDF or purported textbook evidence is seeded.
INSERT INTO textbook(id,course_id,title,edition,editor,publisher,isbn,created_at,updated_at)
VALUES ('textbook-huang-v2-upper','course-math-ii','高等数学（第二版）（上）','第二版·上册','黄立宏','北京大学出版社','9787301349656',strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now'));
INSERT INTO learning_unit(id,course_id,title,status,created_at)
VALUES ('unit-math-limits-pilot','course-math-ii','极限 · 当前试点（待教材匹配）','provisional',strftime('%Y-%m-%dT%H:%M:%fZ','now'));
