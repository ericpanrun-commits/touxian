"""Migration retains original evidence and enforces course boundaries."""
import sqlite3

import pytest

from server.personal_store import PersonalStore,now


def seed_course(c,identity):
    c.execute('INSERT INTO course VALUES (?,?,?)',(identity,identity,identity))
    c.execute('INSERT INTO textbook(id,course_id,title,created_at,updated_at) VALUES (?,?,?,?,?)',
        ('book-'+identity,identity,'Synthetic book',now(),now()))
    c.execute('INSERT INTO learning_unit(id,course_id,title,created_at) VALUES (?,?,?,?)',
        ('unit-'+identity,identity,'Synthetic unit',now()))
    c.execute('''INSERT INTO lecture(id,course_id,title,learning_unit_id,idempotency_key,request_hash,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?)''',('lecture-'+identity,identity,'Synthetic class','unit-'+identity,identity,identity,now(),now()))


def test_upgrade_preserves_every_old_row_and_checks_existing_parent_references(tmp_path,monkeypatch):
    store=PersonalStore(tmp_path/'personal.sqlite3')
    monkeypatch.setattr('server.personal_store.SCHEMA_VERSION',25);store.initialize()
    with store.connection(write=True) as c:
        c.execute('''INSERT INTO lecture(id,course_id,title,idempotency_key,request_hash,created_at,updated_at)
            VALUES ('original-class','course-math-ii','Synthetic original','original','original',?,?)''',(now(),now()))
        c.execute('''INSERT INTO review_artifact(id,lecture_id,version,content_json,provider,model,prompt_version,
            input_versions_json,source_evidence_json,input_hash,status,created_at)
            VALUES ('original-review','original-class',1,'{"text":"Synthetic retained content"}','fixture','fixture',
            'fixture','{}','[]','original','accepted',?)''',(now(),))
        c.execute("UPDATE lecture SET accepted_artifact_id='original-review' WHERE id='original-class'")
        names=[row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='schema_migrations'")]
        before={name:[tuple(row) for row in c.execute('SELECT * FROM "'+name+'" ORDER BY rowid')] for name in names}
    monkeypatch.setattr('server.personal_store.SCHEMA_VERSION',26);store.initialize();store.initialize()
    with store.connection() as c:
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        assert {name:[tuple(row) for row in c.execute('SELECT * FROM "'+name+'" ORDER BY rowid')] for name in names}==before
        assert c.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==26
        assert c.execute("SELECT accepted_artifact_id FROM lecture WHERE id='original-class'").fetchone()[0]=='original-review'
    backup=store.path.with_name(store.path.name+'.before-v26.backup')
    with sqlite3.connect(backup) as c:assert c.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==25


def test_two_courses_can_store_classes_books_and_units_without_crossing_boundaries(tmp_path):
    store=PersonalStore(tmp_path/'personal.sqlite3');store.initialize()
    with store.connection(write=True) as c:
        seed_course(c,'course-engineering');seed_course(c,'course-english')
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE lecture SET learning_unit_id='unit-course-english' WHERE id='lecture-course-engineering'")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE lecture SET course_id='course-english',learning_unit_id=NULL WHERE id='lecture-course-engineering'")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE textbook SET course_id='course-english' WHERE id='book-course-engineering'")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE learning_unit SET course_id='course-english' WHERE id='unit-course-engineering'")
    with store.connection() as c:assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_confirmed_sections_cannot_cross_courses_through_either_side_of_a_link(tmp_path):
    from server.test_documents import text_pdf
    from server.documents.storage import AttachmentStore
    store=PersonalStore(tmp_path/'personal.sqlite3');store.initialize()
    file=text_pdf(tmp_path/'synthetic.pdf',['Synthetic source for course boundary checks.'])
    saved=AttachmentStore(store).collect([(file.name,'application/pdf',file)],'','Synthetic source')
    attachment=saved['snapshot']['attachments'][0]
    with store.connection(write=True) as c:
        seed_course(c,'engineering');seed_course(c,'english')
        c.execute('INSERT INTO document(id,attachment_id,sha256,title,page_count,created_at,updated_at) VALUES (?,?,?,?,1,?,?)',
            ('shared-original',attachment['id'],attachment['sha256'],'Synthetic source',now(),now()))
        c.execute("UPDATE textbook SET document_id='shared-original' WHERE id IN ('book-engineering','book-english')")
        c.execute('''INSERT INTO document_section(id,textbook_id,document_id,learning_unit_id,title,start_page,end_page,created_at)
            VALUES ('section','book-engineering','shared-original','unit-engineering','Synthetic section',1,1,?)''',(now(),))
        c.execute("INSERT INTO lecture_section VALUES ('lecture-engineering','section','confirmed','user',?)",(now(),))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO lecture_section VALUES ('lecture-english','section','confirmed','user',?)",(now(),))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE document_section SET learning_unit_id='unit-english' WHERE id='section'")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE document_section SET textbook_id='book-english',learning_unit_id='unit-english' WHERE id='section'")
    with store.connection() as c:assert not c.execute('PRAGMA foreign_key_check').fetchall()
