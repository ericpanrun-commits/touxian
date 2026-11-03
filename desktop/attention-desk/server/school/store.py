"""Small Course/Lecture/Textbook relationships; originals remain in their owners."""
import json
from uuid import UUID
from ..personal_store import PersonalError, encode, identifier, now
from ..documents.policy import IMAGE_EXTENSIONS
from .documents import digest
from .models import COURSE, TEXTBOOK, require_course

NOTE_EXTENSIONS = ('pdf', *sorted(IMAGE_EXTENSIONS))
NOTE_PLACEHOLDERS = ','.join('?' for _ in NOTE_EXTENSIONS)


def request_id(value):
    try: return str(UUID(value))
    except (ValueError, TypeError, AttributeError): raise PersonalError('请使用有效发送标识。', 'invalid_key', 400)


class SchoolStore:
    def __init__(self, personal, documents):
        self.personal, self.documents = personal, documents

    @staticmethod
    def _lecture(c, lecture_id, version=None):
        row = c.execute('SELECT * FROM lecture WHERE id=?', (lecture_id,)).fetchone()
        if not row: raise PersonalError('课次不存在。', 'not_found', 404)
        require_course(row['course_id'],c)
        if version is not None and row['version'] != version:
            raise PersonalError('课次或来源已更新，请重新读取后核对。', 'conflict', 409)
        return dict(row)

    @staticmethod
    def _request(c, key, operation, signature, result=None):
        old = c.execute('SELECT * FROM school_request WHERE id=?', (key,)).fetchone()
        if old:
            if old['operation'] != operation or old['request_hash'] != signature:
                raise PersonalError('同一发送标识的内容已改变，请开始新的操作。', 'idempotency_conflict', 409)
            return old['result_id']
        if result:
            c.execute('INSERT INTO school_request VALUES (?,?,?,?,?)', (key, operation, signature, result, now()))
        return None

    def textbook(self,textbook_id):
        with self.personal.connection() as c:
            saved=c.execute('SELECT * FROM textbook WHERE id=?',(textbook_id,)).fetchone()
            if not saved:raise PersonalError('教材尚未登记，请先选择这门课的教材。','textbook_required',409)
            row=dict(saved)
            require_course(row['course_id'],c)
            row['document'] = dict(c.execute('SELECT * FROM document WHERE id=?', (row['document_id'],)).fetchone()) if row['document_id'] else None
            row['sections'] = [dict(r) for r in c.execute('SELECT * FROM document_section WHERE textbook_id=? AND document_id IS ? ORDER BY start_page', (textbook_id, row['document_id']))]
            row['real_acceptance'] = 'pending' if not row['document_id'] or row['is_fixture'] else row['acceptance_status']
            row['library_metadata'] = self._library_metadata(c,row['document_id'])
            return row

    def books(self,course_id=None):
        with self.personal.connection() as c:
            if course_id is not None:require_course(course_id,c)
            ids=[row[0] for row in c.execute('SELECT id FROM textbook'+(' WHERE course_id=?' if course_id is not None else '')+
                ' ORDER BY course_id,CASE role WHEN \'primary\' THEN 0 ELSE 1 END,created_at,id',(course_id,) if course_id is not None else ())]
        return [self.textbook(identity) for identity in ids]

    def book(self,course_id=COURSE):
        books=self.books(course_id)
        if not books:return None
        # Preserve the original Math primary and its input fingerprints.
        primary=next((row for row in books if course_id==COURSE and row['id']==TEXTBOOK),books[0])
        return {**primary,'companions':[row for row in books if row['id']!=primary['id']]}

    def register_book(self,payload,key):
        key=request_id(key);data=payload.model_dump()
        for field in ('title','edition','editor','publisher','isbn'):
            value=data[field]
            data[field]=value.strip() if value is not None else None
            if field!='title' and not data[field]:data[field]=None
        if not data['title']:raise PersonalError('请填写教材名称。')
        signature=digest(encode(data))
        with self.personal.connection(write=True) as c:
            require_course(data['course_id'],c)
            identity=self._request(c,key,'textbook',signature)
            if not identity:
                same=c.execute('''SELECT id,role FROM textbook WHERE course_id=? AND title=? AND edition IS ?
                    AND editor IS ? AND publisher IS ? AND isbn IS ?''',tuple(data[field] for field in
                    ('course_id','title','edition','editor','publisher','isbn'))).fetchone()
                if same:
                    if data['role'] is not None and data['role']!=same['role']:
                        raise PersonalError('这份教材已按另一册次角色登记，请核对已有教材。','textbook_role_conflict',409)
                    identity=same['id']
                else:
                    primary=c.execute("SELECT id FROM textbook WHERE course_id=? AND role='primary'",(data['course_id'],)).fetchone()
                    if data['role']=='primary' and primary:
                        raise PersonalError('这门课已有主要教材，可将新教材登记为补充册。','primary_textbook_exists',409)
                    role=data['role'] or ('companion' if primary else 'primary')
                    identity=identifier();stamp=now()
                    c.execute('''INSERT INTO textbook(id,course_id,title,edition,editor,publisher,isbn,role,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)''',(identity,data['course_id'],data['title'],data['edition'],data['editor'],
                        data['publisher'],data['isbn'],role,stamp,stamp))
                self._request(c,key,'textbook',signature,identity)
        return self.textbook(identity)

    @staticmethod
    def _library_metadata(c,document_id):
        row=c.execute('SELECT o.metadata_json FROM textbook_original o JOIN document d ON d.attachment_id=o.attachment_id WHERE d.id=?',(document_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def overview(self, limit=20, offset=0, *,course_id=COURSE):
        with self.personal.connection() as c:
            course = require_course(course_id,c)
            lectures = [dict(r) for r in c.execute('''SELECT l.*,
                (SELECT count(*) FROM lecture_note WHERE lecture_id=l.id) AS note_count,
                (SELECT count(*) FROM review_artifact WHERE lecture_id=l.id) AS review_count
                FROM lecture l WHERE course_id=? ORDER BY lecture_date DESC,created_at DESC LIMIT ? OFFSET ?''', (course_id, limit, offset))]
            total = c.execute('SELECT count(*) FROM lecture WHERE course_id=?', (course_id,)).fetchone()[0]
            units = [dict(r) for r in c.execute('SELECT * FROM learning_unit WHERE course_id=?', (course_id,))]
        return {'course': course, 'textbook': self.book(course_id), 'lectures': lectures, 'total': total, 'units': units, 'limit': limit, 'offset': offset}

    def note_choices(self,course_id=COURSE):
        with self.personal.connection() as c:
            require_course(course_id,c)
            return [dict(r) for r in c.execute('''SELECT i.id AS raw_item_id,i.created_at,r.title_hint,a.filename,a.id AS attachment_id
                FROM raw_item i JOIN raw_snapshot s ON s.id=COALESCE(i.inbox_original_snapshot_id,
                    (SELECT id FROM raw_snapshot WHERE raw_item_id=i.id ORDER BY created_at LIMIT 1))
                JOIN raw_review r ON r.raw_snapshot_id=s.id JOIN snapshot_attachment sa ON sa.raw_snapshot_id=s.id
                JOIN attachment a ON a.id=sa.attachment_id WHERE i.inbox_course_id=? AND i.kind!='inbox_textbook' AND a.extension IN ('''+NOTE_PLACEHOLDERS+''') AND a.local_path IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM document d JOIN textbook t ON t.document_id=d.id WHERE d.sha256=a.sha256)
                ORDER BY i.created_at DESC LIMIT 100''', (course_id, *NOTE_EXTENSIONS))]

    def _note_document(self, raw_id, course_id=COURSE,*,require_ready=True):
        with self.personal.connection() as c:
            require_course(course_id,c)
            row = c.execute('SELECT * FROM raw_item WHERE id=?', (raw_id,)).fetchone()
            if not row or row['inbox_course_id'] != course_id:
                raise PersonalError('请先把这份笔记关联到同一门课程。', 'course_link_required', 409)
            if row['kind'] == 'wechat_batch':
                raise PersonalError('请先通过 Inbox 主动提交课堂笔记 PDF 或图片。', 'notes_required', 400)
            if row['kind'] == 'inbox_textbook':
                raise PersonalError('教材不能当作课堂笔记，请选择 Apple Notes 等课堂输入。','notes_required',400)
            atts = c.execute('SELECT id FROM attachment WHERE raw_item_id=? AND extension IN ('+NOTE_PLACEHOLDERS+') AND local_path IS NOT NULL ORDER BY created_at', (raw_id, *NOTE_EXTENSIONS)).fetchall()
            if len(atts) != 1: raise PersonalError('一条课次笔记请先选择一份 PDF 或图片输入。', 'single_notes_pdf', 400)
            attachment_id = atts[0][0]
        doc = self.documents.register(attachment_id)
        with self.personal.connection() as c:
            if c.execute('SELECT 1 FROM textbook WHERE document_id=?',(doc['id'],)).fetchone():
                raise PersonalError('教材不能当作课堂笔记，请选择实际课堂输入。','notes_required',400)
        if require_ready and doc['status'] != 'ready':
            raise PersonalError('原件已保存，请先完成笔记页数登记或本地解析。', 'notes_catalog_required', 409)
        return doc

    def confirm_note_lecture(self, raw_id, lecture_id,*,source_guard=None):
        """Explicit owner correction; preserve all originals and other inputs."""
        with self.personal.connection() as c:target=self._lecture(c,lecture_id)
        doc = self._note_document(raw_id,target['course_id'])
        with self.personal.connection(write=True) as c:
            if source_guard:source_guard(c)
            self._lecture(c, lecture_id)
            self._check_note_course(c,raw_id,target['course_id'])
            previous = [row[0] for row in c.execute('''SELECT lecture_id FROM lecture_note
                WHERE document_id=? AND raw_item_id=? AND lecture_id!=?''', (doc['id'], raw_id, lecture_id))]
            c.execute('DELETE FROM lecture_note WHERE document_id=? AND raw_item_id=? AND lecture_id!=?', (doc['id'], raw_id, lecture_id))
            linked = c.execute('INSERT OR IGNORE INTO lecture_note VALUES (?,?,?)', (lecture_id, doc['id'], raw_id))
            affected = set(previous)
            if linked.rowcount: affected.add(lecture_id)
            for identity in affected:
                c.execute('UPDATE lecture SET version=version+1,updated_at=? WHERE id=?', (now(), identity))
        return self.lecture(lecture_id)

    @staticmethod
    def _check_note_course(c,raw_id,course_id):
        row=c.execute('SELECT inbox_course_id FROM raw_item WHERE id=?',(raw_id,)).fetchone()
        if not row or row[0]!=course_id:
            raise PersonalError('笔记的课程关联已有变化，请重新读取后再确认。','lecture_context_mismatch',409)

    def note_attachment(self,document_id,raw_id,course_id):
        """An immutable document may be reused by distinct real submissions."""
        with self.personal.connection() as c:
            require_course(course_id,c)
            self._check_note_course(c,raw_id,course_id)
            rows=c.execute('''SELECT a.* FROM attachment a JOIN document d ON d.sha256=a.sha256
                JOIN snapshot_attachment sa ON sa.attachment_id=a.id AND sa.raw_snapshot_id=a.origin_snapshot_id
                WHERE d.id=? AND a.raw_item_id=? AND a.local_path IS NOT NULL ORDER BY a.created_at,a.id''',
                (document_id,raw_id)).fetchall()
            if len(rows)!=1:
                raise PersonalError('这份笔记的原件关联尚不能唯一核对，原件和已有课堂已保留。','note_source_mismatch',409)
            return dict(rows[0])

    def create_lecture(self, payload, key,*,automatic_note=False,source_guard=None,replace_note_link=False):
        key = request_id(key)
        data = payload.model_dump(); data['title'] = data['title'].strip()
        if not data['title']: raise PersonalError('请填写课堂主题。')
        signature = digest(encode(data))
        with self.personal.connection() as c:
            require_course(payload.course_id,c)
            existing = self._request(c, key, 'lecture', signature)
        if existing: return self.lecture(existing)
        doc = self._note_document(data['notes_raw_item_id'],data['course_id'])
        with self.personal.connection(write=True) as c:
            if source_guard:source_guard(c)
            require_course(data['course_id'],c)
            self._check_note_course(c,data['notes_raw_item_id'],data['course_id'])
            existing = self._request(c, key, 'lecture', signature)
            if existing: lecture_id = existing
            else:
                unit = data['learning_unit_id']
                if unit and not c.execute('SELECT 1 FROM learning_unit WHERE id=? AND course_id=?', (unit, data['course_id'])).fetchone():
                    raise PersonalError('教学单元不属于这门课程。', 'invalid_reference', 400)
                same = c.execute('''SELECT l.id FROM lecture l JOIN lecture_note n ON n.lecture_id=l.id
                    WHERE l.course_id=? AND l.lecture_date IS ? AND l.title=? AND n.document_id=?''',
                    (data['course_id'], data['lecture_date'], data['title'], doc['id'])).fetchone()
                if automatic_note:
                    today=c.execute('SELECT id FROM lecture WHERE course_id=? AND lecture_date IS ?',(data['course_id'],data['lecture_date'])).fetchall()
                    if len(today)>1:raise PersonalError('当天存在多个课次，请确认一次，未自动重复创建。','lecture_context_ambiguous',409)
                    if today:same=today[0]
                if same: lecture_id = same[0]
                else:
                    lecture_id, stamp = identifier(), now()
                    c.execute('''INSERT INTO lecture(id,course_id,lecture_date,title,sequence_number,learning_unit_id,
                        idempotency_key,request_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                        (lecture_id, data['course_id'], data['lecture_date'], data['title'], data['sequence_number'], unit, key, signature, stamp, stamp))
                if replace_note_link:
                    previous=[row[0] for row in c.execute('SELECT lecture_id FROM lecture_note WHERE document_id=? AND raw_item_id=? AND lecture_id!=?',
                        (doc['id'],data['notes_raw_item_id'],lecture_id))]
                    c.execute('DELETE FROM lecture_note WHERE document_id=? AND raw_item_id=? AND lecture_id!=?',
                        (doc['id'],data['notes_raw_item_id'],lecture_id))
                    for prior in previous:c.execute('UPDATE lecture SET version=version+1,updated_at=? WHERE id=?',(now(),prior))
                linked=c.execute('INSERT OR IGNORE INTO lecture_note VALUES (?,?,?)', (lecture_id, doc['id'], data['notes_raw_item_id']))
                if same and linked.rowcount:c.execute('UPDATE lecture SET version=version+1,updated_at=? WHERE id=?',(now(),lecture_id))
                self._request(c, key, 'lecture', signature, lecture_id)
        return self.lecture(lecture_id)

    def lecture(self, lecture_id):
        with self.personal.connection() as c:
            row = self._lecture(c, lecture_id)
            row['notes'] = [dict(r) for r in c.execute('''SELECT d.*,n.raw_item_id,a.mime_type,a.extension FROM lecture_note n
                JOIN document d ON d.id=n.document_id JOIN attachment a ON a.id=d.attachment_id WHERE n.lecture_id=?''', (lecture_id,))]
            row['sections'] = [dict(r) for r in c.execute('''SELECT s.*,ls.status AS link_status,ls.origin FROM lecture_section ls
                JOIN document_section s ON s.id=ls.section_id JOIN textbook t ON t.id=s.textbook_id
                WHERE ls.lecture_id=? AND ls.status!='removed' AND s.document_id=t.document_id ORDER BY s.start_page''', (lecture_id,))]
            row['artifacts'] = [dict(r) for r in c.execute('''SELECT id,version,status,model,prompt_version,generated_at,created_at,cost_json,error_json,
                content_json IS NOT NULL AS has_content
                FROM review_artifact WHERE lecture_id=? ORDER BY version DESC''', (lecture_id,))]
            for artifact in row['artifacts']:artifact['has_content'] = bool(artifact['has_content'])
            row['jobs'] = [dict(r) for r in c.execute('''SELECT * FROM attachment_job WHERE json_extract(school_payload_json,'$.lecture_id')=?
                ORDER BY created_at DESC LIMIT 10''', (lecture_id,))]
            row['mistakes'] = {'available': False, 'items': [], 'message': '当前没有足够历史错误数据'}
            row['course']=require_course(row['course_id'],c)
        selected=sorted({section['textbook_id'] for section in row['sections'] if section['link_status']=='confirmed'})
        if row['course_id']!=COURSE and selected:
            # A later unrelated primary-book registration cannot change an
            # existing class's already selected evidence frame.
            primary=self.textbook(selected[0])
            row['textbook']={**primary,'companions':[book for book in self.books(row['course_id']) if book['id']!=primary['id']]}
        else:row['textbook'] = self.book(row['course_id'])
        return row

    def link_book(self, raw_id, version, file_role, *, fixture=False,textbook_id=TEXTBOOK):
        prepared=self.prepare_book_file(raw_id,textbook_id)
        return self.link_registered_book(raw_id,version,file_role,**prepared,fixture=fixture,textbook_id=textbook_id)

    def prepare_book_file(self,raw_id,textbook_id):
        """Register the immutable original before an atomic metadata command."""
        book=self.textbook(textbook_id)
        with self.personal.connection() as c:
            raw=c.execute('SELECT inbox_course_id FROM raw_item WHERE id=?',(raw_id,)).fetchone()
            if not raw or raw[0] is not None and raw[0]!=book['course_id']:
                raise PersonalError('请选择这门课的教材原件，课程关联不能混用。','course_link_required',409)
            atts = c.execute("SELECT * FROM attachment WHERE raw_item_id=? AND extension='pdf' AND local_path IS NOT NULL ORDER BY created_at", (raw_id,)).fetchall()
            if len(atts) != 1: raise PersonalError('请先保存一份教材 PDF。', 'single_textbook_pdf', 400)
            attachment = atts[0]['id']
        doc = self.documents.register(attachment)
        return {'attachment_id':attachment,'document_id':doc['id']}

    def link_registered_book(self,raw_id,version,file_role,*,attachment_id,document_id,fixture=False,textbook_id=TEXTBOOK):
        """SQL-only commit boundary, shared by local and durable cloud actions."""
        with self.personal.connection(write=True) as c:
            current = c.execute('SELECT * FROM textbook WHERE id=?', (textbook_id,)).fetchone()
            if not current:raise PersonalError('教材尚未登记。','textbook_required',409)
            raw=c.execute('SELECT inbox_course_id FROM raw_item WHERE id=?',(raw_id,)).fetchone()
            if not raw or raw[0] is not None and raw[0]!=current['course_id']:
                raise PersonalError('原件的课程关联已有变化，请重新读取后再保存。','course_link_required',409)
            doc=c.execute('''SELECT d.* FROM document d JOIN attachment a ON a.sha256=d.sha256
                JOIN snapshot_attachment sa ON sa.attachment_id=a.id AND sa.raw_snapshot_id=a.origin_snapshot_id
                WHERE d.id=? AND a.id=? AND a.raw_item_id=? AND a.extension='pdf' AND a.local_path IS NOT NULL''',
                (document_id,attachment_id,raw_id)).fetchone()
            if not doc:raise PersonalError('教材原件关联已有变化，请重新读取。','book_source_mismatch',409)
            if current['document_id'] == doc['id']:
                return self.textbook(textbook_id)
            if current['version'] != version: raise PersonalError('教材状态已更新，请重新读取。', 'conflict', 409)
            c.execute('''UPDATE textbook SET document_id=?,file_role=?,status=?,is_fixture=?,version=version+1,
                acceptance_status='pending_evaluation',updated_at=? WHERE id=?''',
                      (doc['id'], file_role, 'ready' if doc['status'] == 'ready' else 'cataloging', int(fixture), now(), textbook_id))
        return self.textbook(textbook_id)

    def add_section(self, payload):
        if not payload.title.strip(): raise PersonalError('请填写章节名称。')
        book = self.textbook(payload.textbook_id)
        if payload.expected_version is not None and payload.expected_version!=book['version']:
            raise PersonalError('教材已有更新，请重新读取后再选择章节。','conflict',409)
        if not book['document'] or book['document']['status'] != 'ready':
            raise PersonalError('请先完成教材页数登记。', 'catalog_required', 409)
        if payload.end_page < payload.start_page or payload.end_page > book['document']['page_count']:
            raise PersonalError('章节页码范围不属于这份 PDF。', 'invalid_page_range', 400)
        if payload.end_page - payload.start_page >= 80:
            raise PersonalError('每次最多选择 80 页，请分成小节处理。', 'chapter_scope', 400)
        with self.personal.connection(write=True) as c:
            current=c.execute('SELECT version,document_id FROM textbook WHERE id=?',(book['id'],)).fetchone()
            if not current or current['version']!=book['version'] or current['document_id']!=book['document_id']:
                raise PersonalError('教材已有更新，章节尚未保存，请重新读取。','conflict',409)
            if payload.learning_unit_id and not c.execute('SELECT 1 FROM learning_unit WHERE id=? AND course_id=?', (payload.learning_unit_id, book['course_id'])).fetchone():
                raise PersonalError('教学单元不属于这门课程。', 'invalid_reference', 400)
            old = c.execute('''SELECT id FROM document_section WHERE textbook_id=? AND document_id=? AND title=? AND start_page=? AND end_page=? AND learning_unit_id IS ?''',
                            (book['id'],book['document_id'], payload.title.strip(), payload.start_page, payload.end_page,payload.learning_unit_id)).fetchone()
            if old: section_id = old[0]
            else:
                section_id = identifier()
                c.execute('INSERT INTO document_section VALUES (?,?,?,?,?,?,?,1,?)',
                          (section_id, book['id'], book['document_id'], payload.learning_unit_id, payload.title.strip(), payload.start_page, payload.end_page, now()))
        return next(s for s in self.textbook(book['id'])['sections'] if s['id'] == section_id)

    def link_sections(self, lecture_id, version, section_ids,*,source_guard=None):
        with self.personal.connection(write=True) as c:
            if source_guard:source_guard(c)
            lecture = self._lecture(c, lecture_id, version)
            for sid in set(section_ids):
                if not c.execute('''SELECT 1 FROM document_section s JOIN textbook t ON t.id=s.textbook_id
                    WHERE s.id=? AND t.course_id=? AND s.document_id=t.document_id''', (sid, lecture['course_id'])).fetchone():
                    raise PersonalError('章节不属于这门课的当前教材版本。', 'invalid_reference', 400)
            c.execute("UPDATE lecture_section SET status='removed',updated_at=? WHERE lecture_id=?", (now(), lecture_id))
            for sid in set(section_ids):
                c.execute('''INSERT INTO lecture_section VALUES (?,?,'confirmed','user',?) ON CONFLICT(lecture_id,section_id)
                    DO UPDATE SET status='confirmed',origin='user',updated_at=excluded.updated_at''', (lecture_id, sid, now()))
            c.execute("UPDATE lecture SET status='ready',version=version+1,updated_at=? WHERE id=?", (now(), lecture_id))
        return self.lecture(lecture_id)
