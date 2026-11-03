"""Named School operations in the existing attachment_job ledger."""
import json
from ..personal_store import PersonalError, encode, identifier, now
from .documents import digest
from .store import SchoolStore, request_id


class SchoolJobs:
    def __init__(self, personal): self.personal = personal

    @staticmethod
    def public(row):
        result = dict(row)
        result['payload'] = json.loads(result.pop('school_payload_json') or '{}')
        result['result'] = json.loads(result.pop('school_result_json') or 'null')
        result['errors'] = json.loads(result.pop('error_json') or 'null')
        return result

    def get(self, job_id):
        with self.personal.connection() as c:
            row = c.execute("SELECT * FROM attachment_job WHERE id=? AND operation LIKE 'school_%'", (job_id,)).fetchone()
            if not row: raise PersonalError('学习处理记录不存在。', 'not_found', 404)
            return self.public(row)

    @staticmethod
    def _resume_interrupted_local(c,row):
        if row['operation'] not in {'school_catalog','school_index'} or row['status']!='recovery_needed':return False
        if c.execute('SELECT 1 FROM school_ai_attempt WHERE job_id=? LIMIT 1',(row['id'],)).fetchone():return False
        document=json.loads(row['school_payload_json'] or '{}').get('document_id')
        if not document:return False
        if c.execute('''SELECT 1 FROM attachment_job WHERE id!=? AND operation=? AND status IN ('waiting','running')
            AND json_extract(school_payload_json,'$.document_id')=? LIMIT 1''',(row['id'],row['operation'],document)).fetchone():return False
        return c.execute("UPDATE attachment_job SET status='waiting',phase='恢复本地处理',error_json=NULL,completed_at=NULL WHERE id=? AND status='recovery_needed'",(row['id'],)).rowcount==1

    def create(self, operation, payload, attachment, fingerprint, key, *, allow_local_retry=False, request_signature=None,resume_interrupted_local=False):
        key = request_id(key)
        signature = request_signature or digest(encode({'operation': operation, 'payload': payload, 'retry': allow_local_retry}))
        with self.personal.connection(write=True) as c:
            old = SchoolStore._request(c, key, operation, signature)
            if old:
                row=c.execute('SELECT * FROM attachment_job WHERE id=?',(old,)).fetchone()
                resumed=resume_interrupted_local and self._resume_interrupted_local(c,row)
                return self.public(c.execute('SELECT * FROM attachment_job WHERE id=?',(old,)).fetchone()),bool(resumed)
            same = c.execute('SELECT * FROM attachment_job WHERE school_fingerprint=?', (fingerprint,)).fetchone()
            if same:
                job_id = same['id']
                resumed=resume_interrupted_local and self._resume_interrupted_local(c,same)
                retry = not resumed and allow_local_retry and operation in {'school_catalog', 'school_index'} and same['status'] in {'failed', 'partial', 'recovery_needed'}
                if retry:
                    c.execute("UPDATE attachment_job SET status='waiting',phase='等待恢复本地处理',error_json=NULL,completed_at=NULL WHERE id=?", (job_id,))
                SchoolStore._request(c, key, operation, signature, job_id)
                return self.public(c.execute('SELECT * FROM attachment_job WHERE id=?', (job_id,)).fetchone()), bool(retry or resumed)
            scope_key = 'lecture_id' if operation == 'school_review' else 'document_id'
            scope = payload.get(scope_key)
            active = c.execute('''SELECT * FROM attachment_job WHERE operation=? AND status IN ('waiting','running')
                AND json_extract(school_payload_json,?)=? LIMIT 1''', (operation, '$.' + scope_key, scope)).fetchone()
            if active:
                raise PersonalError('已有这一课次或文档的处理正在运行，请结束后再发起新的范围。', 'operation_running', 409)
            job_id = identifier()
            c.execute('''INSERT INTO attachment_job(id,raw_item_id,source_snapshot_id,attachment_ids_json,operation,status,phase,
                created_at,school_payload_json,school_fingerprint,school_request_id) VALUES (?,?,?,?,?,'waiting','等待处理',?,?,?,?)''',
                (job_id, attachment['raw_item_id'], attachment['origin_snapshot_id'], encode([attachment['id']]), operation,
                 now(), encode(payload), fingerprint, key))
            SchoolStore._request(c, key, operation, signature, job_id)
            return self.public(c.execute('SELECT * FROM attachment_job WHERE id=?', (job_id,)).fetchone()), True

    def update(self, job_id, status, phase, *, result=None, errors=None, payload=None):
        with self.personal.connection(write=True) as c:
            c.execute('''UPDATE attachment_job SET status=?,phase=?,school_result_json=COALESCE(?,school_result_json),error_json=?,
                school_payload_json=COALESCE(?,school_payload_json),completed_at=? WHERE id=?''',
                (status, phase, encode(result) if result is not None else None, encode(errors) if errors else None,
                 encode(payload) if payload is not None else None, now() if status not in {'waiting', 'running'} else None, job_id))

    def recover(self):
        with self.personal.connection(write=True) as c:
            # DocumentService already marks in-flight shared jobs interrupted.
            rows = c.execute("SELECT * FROM attachment_job WHERE operation LIKE 'school_%' AND (status IN ('waiting','running') OR (status='failed' AND error_json LIKE '%interrupted%'))").fetchall()
            for row in rows:
                error = {'code': 'interrupted', 'message': '处理被重启中断；原件和已成功页面保留。请明确恢复，未知模型结果不会自动重放。'}
                c.execute("UPDATE attachment_job SET status='recovery_needed',phase='需要恢复',error_json=?,completed_at=? WHERE id=?", (encode([error]), now(), row['id']))
                c.execute("UPDATE school_ai_attempt SET status='unknown',error_json=?,completed_at=? WHERE job_id=? AND status='running'", (encode(error), now(), row['id']))
                c.execute("UPDATE document_page SET vision_status='unknown' WHERE vision_attempt_id IN (SELECT id FROM school_ai_attempt WHERE job_id=? AND status IN ('unknown','prepared'))", (row['id'],))
                c.execute("UPDATE review_artifact SET status='recovery_needed',error_json=? WHERE job_id=? AND content_json IS NULL", (encode(error), row['id']))
