"""Append-only content versions and explicit adopted-revision feedback."""
import json
from ..personal_store import PersonalError, encode, identifier, now
from .store import SchoolStore


class ReviewArtifacts:
    def __init__(self, personal): self.personal = personal

    def daily_queue(self):
        """One current reading action per class, without changing version history.

        Classroom selection separately honors explicit adoption. The daily list
        surfaces the newest readable candidate, including a new unadopted one.
        Reading progress must never make an older version reappear as a task.
        """
        with self.personal.connection() as c:
            rows = c.execute('''SELECT r.*,l.accepted_artifact_id,a.version AS adopted_version
                FROM review_artifact r JOIN lecture l ON l.id=r.lecture_id
                LEFT JOIN review_artifact a ON a.id=l.accepted_artifact_id
                WHERE r.status NOT IN ('rejected','superseded')
                ORDER BY l.lecture_date DESC,l.id,r.version DESC,r.id''').fetchall()
        groups = {}
        for row in rows:
            groups.setdefault(row['lecture_id'], []).append(row)
        members = []
        for lecture_id, versions in groups.items():
            readable = [row for row in versions if json.loads(row['content_json'] or 'null')]
            selected = (readable or versions)[0]
            members.append({
                'review_id': selected['id'], 'lecture_id': lecture_id,
                'version': selected['version'], 'has_content': bool(readable),
                'adopted_review_id': selected['accepted_artifact_id'],
                'newer_than_adopted': bool(readable and selected['adopted_version'] is not None
                    and selected['version'] > selected['adopted_version']),
                'newer_unfinished_version': versions[0]['version'] if versions[0]['version'] > selected['version'] else None,
            })
        return {'schema_version': 1, 'members': members}

    def cached(self, lecture_id, fingerprint):
        with self.personal.connection() as c:
            row = c.execute('''SELECT * FROM review_artifact WHERE lecture_id=? AND input_hash=?
                AND content_json IS NOT NULL ORDER BY version DESC LIMIT 1''', (lecture_id, fingerprint)).fetchone()
            return dict(row) if row else None

    def reserve(self, lecture_id, context, model, prompt_version, fingerprint, job_id):
        with self.personal.connection(write=True) as c:
            SchoolStore._lecture(c, lecture_id)
            old = c.execute('SELECT id FROM review_artifact WHERE job_id=?', (job_id,)).fetchone()
            if old: return old[0]
            version = c.execute('SELECT COALESCE(max(version),0)+1 FROM review_artifact WHERE lecture_id=?', (lecture_id,)).fetchone()[0]
            aid = identifier()
            c.execute('''INSERT INTO review_artifact(id,lecture_id,version,provider,model,prompt_version,input_versions_json,
                source_evidence_json,input_hash,status,job_id,created_at) VALUES (?,?,?,'deepseek',?,?,?,?,?,'processing',?,?)''',
                (aid, lecture_id, version, model, prompt_version, encode(context['input_versions']), encode(context['sources']), fingerprint, job_id, now()))
            return aid

    def record_generation(self, artifact_id, metadata):
        with self.personal.connection(write=True) as c:
            row = c.execute('SELECT input_versions_json,content_json FROM review_artifact WHERE id=?', (artifact_id,)).fetchone()
            if not row or row['content_json'] is not None:
                raise PersonalError('已保存的复盘内容版本不能修改生成记录。', 'conflict', 409)
            versions = json.loads(row['input_versions_json'])
            versions['generation'] = metadata
            c.execute('UPDATE review_artifact SET input_versions_json=? WHERE id=?', (encode(versions), artifact_id))

    def finish(self, artifact_id, content, evidence, cost, *, error=None, requires_review=False):
        with self.personal.connection(write=True) as c:
            row = c.execute('SELECT * FROM review_artifact WHERE id=?', (artifact_id,)).fetchone()
            if not row: raise PersonalError('复盘版本不存在。', 'not_found', 404)
            c.execute('''UPDATE review_artifact SET content_json=?,source_evidence_json=?,generated_at=?,status=?,cost_json=?,error_json=? WHERE id=?''',
                      (encode(content) if content else None, encode(evidence), now(), 'needs_review' if content or requires_review else 'failed', encode(cost), encode(error) if error else None, artifact_id))
            # Do not overwrite a different lecture edit that happened during generation.
            input_lecture = json.loads(row['input_versions_json'])['lecture']
            c.execute("UPDATE lecture SET status=CASE WHEN accepted_artifact_id IS NOT NULL THEN 'accepted' ELSE 'needs_review' END,updated_at=? WHERE id=? AND version=?",
                      (now(), row['lecture_id'], input_lecture['version']))

    def get(self, artifact_id):
        with self.personal.connection() as c:
            row = c.execute('SELECT * FROM review_artifact WHERE id=?', (artifact_id,)).fetchone()
            if not row: raise PersonalError('复盘版本不存在。', 'not_found', 404)
            value = dict(row)
            for column, name in [('content_json', 'content'), ('source_evidence_json', 'sources'), ('input_versions_json', 'input_versions'), ('cost_json', 'cost'), ('error_json', 'error')]:
                value[name] = json.loads(value.pop(column) or 'null')
            value['feedback'] = [dict(r) for r in c.execute('SELECT * FROM review_feedback WHERE artifact_id=? ORDER BY created_at', (artifact_id,))]
            lecture = SchoolStore._lecture(c, value['lecture_id'])
            value['adopted'] = lecture['accepted_artifact_id'] == artifact_id
            value['lecture_version_changed'] = any(lecture[k] != v for k, v in value['input_versions']['lecture'].items() if k != 'version')
            original = value['input_versions']
            value['generation'] = original.get('generation')
            original_book = original.get('textbook')
            original_books = original.get('textbooks', [original_book] if original_book else [])
            current_books = {b['id']: c.execute('SELECT * FROM textbook WHERE id=?', (b['id'],)).fetchone() for b in original_books}
            current_book = current_books.get(original_book['id']) if original_book else None
            current_sections = [dict(r) for r in c.execute("SELECT s.id,s.version FROM lecture_section ls JOIN document_section s ON s.id=ls.section_id JOIN textbook t ON t.id=s.textbook_id WHERE ls.lecture_id=? AND ls.status='confirmed' AND s.document_id=t.document_id",(lecture['id'],))]
            if 'textbooks' in original:
                # Adding an unrelated book or unused chapter does not rewrite a
                # notes-only review's historical evidence scope.
                section_versions = {s['id']: s['version'] for s in current_sections}
                changed = any(section_versions.get(s['id']) != s['version'] for s in original['sections'])
                note_ids = {r[0] for r in c.execute('SELECT document_id FROM lecture_note WHERE lecture_id=?', (lecture['id'],))}
                changed = changed or note_ids != {d['id'] for d in original['documents']}
            else:
                changed = sorted(current_sections,key=lambda s:s['id']) != sorted(original['sections'],key=lambda s:s['id'])
            for binding in original_books:
                current = current_books[binding['id']]
                changed = changed or not current or any(current[k] != binding[k] for k in ('version','document_id'))
                if current and current['document_id'] and binding.get('document_version') is not None:
                    document = c.execute('SELECT version FROM document WHERE id=?', (current['document_id'],)).fetchone()
                    changed = changed or not document or document[0] != binding['document_version']
            for document in original['documents']:
                current=c.execute('SELECT version FROM document WHERE id=?',(document['id'],)).fetchone()
                changed = changed or not current or current[0] != document['version']
            value['lecture_version_changed'] = value['lecture_version_changed'] or bool(changed)
            from ..learning.activities import before_lecture,lecture_cycle
            old_preview=original.get('before_lecture')
            if old_preview is not None:old_preview={'user_confirmed_before':0,**old_preview}
            current_cycle=lecture_cycle(self.personal,value['lecture_id'])
            new_cycle_evidence=bool(current_cycle and (current_cycle['during_lecture']['counts']['total'] or current_cycle['after_lecture']['delayed_independent_successes']))
            value['learning_cycle_changed']=original.get('learning_cycle')!=current_cycle if 'learning_cycle' in original else new_cycle_evidence
            value['preview_evidence_changed']=old_preview!=before_lecture(self.personal,value['lecture_id']) or value['learning_cycle_changed']
            value['is_fixture'] = any(bool(b['is_fixture']) for b in original_books)
            if 'textbooks' in original:
                same = all(current_books[b['id']] and current_books[b['id']]['document_id'] == b['document_id'] for b in original_books)
                value['real_textbook_acceptance'] = ('not_used' if not original_books else 'pending' if value['is_fixture'] else
                    'historical_source' if not same else 'accepted' if all(current_books[b['id']]['acceptance_status'] == 'accepted' for b in original_books) else 'pending')
            else:
                value['real_textbook_acceptance'] = 'pending' if value['is_fixture'] else current_book['acceptance_status'] if current_book and current_book['document_id']==original_book['document_id'] else 'historical_source'
            if not value['content'] and not value['lecture_version_changed'] and not value['preview_evidence_changed']:
                from .source_correction import descriptor
                value['source_correction']=descriptor(c,artifact_id)
            return value

    def feedback(self, artifact_id, payload):
        with self.personal.connection(write=True) as c:
            artifact = c.execute('SELECT * FROM review_artifact WHERE id=?', (artifact_id,)).fetchone()
            if not artifact: raise PersonalError('复盘版本不存在。', 'not_found', 404)
            lecture = SchoolStore._lecture(c, artifact['lecture_id'], payload.expected_lecture_version)
            if payload.action != 'comment' and not artifact['content_json']:
                raise PersonalError('未完成的复盘不能采用或拒绝。', 'review_incomplete', 409)
            c.execute('INSERT INTO review_feedback VALUES (?,?,?,?,?)', (identifier(), artifact_id, payload.action, payload.feedback, now()))
            if payload.action == 'accept':
                c.execute("UPDATE review_artifact SET status='superseded' WHERE lecture_id=? AND status='accepted' AND id!=?", (lecture['id'], artifact_id))
                c.execute("UPDATE review_artifact SET status='accepted' WHERE id=?", (artifact_id,))
                c.execute("UPDATE lecture SET accepted_artifact_id=?,status='accepted',version=version+1,updated_at=? WHERE id=?", (artifact_id, now(), lecture['id']))
            elif payload.action == 'reject':
                c.execute("UPDATE review_artifact SET status='rejected' WHERE id=?", (artifact_id,))
                c.execute('''UPDATE lecture SET accepted_artifact_id=CASE WHEN accepted_artifact_id=? THEN NULL ELSE accepted_artifact_id END,
                    status=CASE WHEN accepted_artifact_id=? THEN 'needs_review' ELSE status END,version=version+1,updated_at=? WHERE id=?''', (artifact_id, artifact_id, now(), lecture['id']))
        return self.get(artifact_id)
