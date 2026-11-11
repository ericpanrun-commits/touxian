"""Read-only Inbox status from the existing note capture and saved review."""
import json

from ..recovery_controls import paid_ai_paused
from .store import capture_projection


def note_processing(connection, raw, snapshot_id, *, recovery_database_path=None):
    if raw.get('kind') != 'inbox_lecture_note':
        return None
    capture = connection.execute('''SELECT * FROM learning_capture
        WHERE raw_item_id=? AND snapshot_id=?
        ORDER BY created_at DESC,id DESC LIMIT 1''', (raw['id'], snapshot_id)).fetchone()
    if not capture:
        return None
    capture = capture_projection(connection, capture, include_lecture_choices=False)
    context = capture['context'] or {}
    work = context.get('note_automation') or {}
    state = work.get('status') or capture['status']
    message = work.get('message') or ''
    result = {'capture_id': capture['id'], 'snapshot_id': snapshot_id,
              'status': state, 'message': message, 'summary': None, 'review_id': None}
    if state == 'completed':
        review = connection.execute('''SELECT id,status,content_json,error_json FROM review_artifact
            WHERE id=? AND lecture_id=?''', (work.get('review_id'), context.get('lecture_id') or work.get('lecture_id'))).fetchone()
        content = json.loads(review['content_json'] or 'null') if review else None
        if review and not review['error_json'] and review['status'] not in {'failed', 'rejected', 'superseded'} and isinstance(content, dict) and content:
            claims = content.get('notes_taught') or []
            texts = [entry.get('text', '') for entry in claims if isinstance(entry, dict)]
            summary = '；'.join(text for text in texts if isinstance(text, str) and text.strip())[:240]
            result.update(status='completed', review_id=review['id'], summary=summary or None,
                          message='笔记分析已完成，复盘待核对。' if review['status'] == 'needs_review' else '笔记分析已完成。')
        else:
            result.update(status='needs_review', message='笔记整理记录已保存，但可读分析结果尚未确认，请核对。')
    elif state == 'needs_context':
        result['message'] = message or '笔记已保存，请确认课程或课堂日期后继续。'
    elif state in {'failed', 'interrupted'}:
        result.update(status='failed', message=message or '笔记处理未完成，原件保留，请核对后重试。')
    elif state in {'needs_review', 'needs_source', 'needs_vision_permission'}:
        result.update(status='needs_review', message=message or '笔记原件已保存，仍需核对处理条件。')
        if state == 'needs_review' and work.get('review_id'):
            review = connection.execute('SELECT error_json FROM review_artifact WHERE id=? AND lecture_id=?',
                (work['review_id'], context.get('lecture_id') or work.get('lecture_id'))).fetchone()
            failure = json.loads(review['error_json'] or '{}') if review else {}
            if isinstance(failure, dict) and failure.get('code') == 'review_validation':
                labels = {'conditions_required': '部分方法缺少适用条件依据',
                    'notes_evidence': '部分笔记结论缺少笔记来源引用',
                    'unreliable_method': '方法引用了尚未核对的识别内容',
                    'unreliable_answer': '自测答案引用了尚未核对的识别内容',
                    'source_role': '笔记与教材的来源标注不符',
                    'unbound_math': '数学表述未绑定原文依据'}
                reasons = list(dict.fromkeys(labels[issue['code']] for issue in failure.get('issues', [])
                    if isinstance(issue, dict) and issue.get('code') in labels))[:2]
                result['message'] = '复盘未通过来源校验' + ('：' + '；'.join(reasons) if reasons else '') + '。原件与识别结果已保留，请核对后继续。'
    else:
        database = recovery_database_path or connection.execute('PRAGMA database_list').fetchone()[2]
        if paid_ai_paused(database) or state == 'paused':
            result.update(status='paused', message='笔记已保存，AI 处理已暂停；已有结果保留。')
        elif state == 'needs_setup':
            result.update(status='needs_review', message=message or '笔记已保存，分析服务尚未就绪。')
        elif state in {'received', 'local_ready', 'reviewing', 'processing', 'generating'}:
            job = connection.execute('SELECT status,error_json FROM attachment_job WHERE id=?',
                (work['review_job_id'],)).fetchone() if work.get('review_job_id') else None
            if job and job['status'] in {'waiting', 'running'}:
                result.update(status='processing', message='正在处理这份笔记。')
            elif job and job['status'] == 'failed':
                errors = json.loads(job['error_json'] or '{}')
                entries = errors if isinstance(errors, list) else [errors]
                reason = next((entry['message'] for entry in entries
                               if isinstance(entry, dict) and isinstance(entry.get('message'), str) and entry['message'].strip()), None)
                result.update(status='failed', message=reason or '笔记处理未完成，原件保留，请核对后重试。')
            else:
                result.update(status='needs_review', message='笔记已保存，尚无已确认的 AI 分析结果。')
        else:
            result.update(status='needs_review', message=message or '笔记已保存，请核对处理状态。')
    return result
