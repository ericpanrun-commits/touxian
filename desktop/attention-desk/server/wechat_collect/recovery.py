"""Read-only backlog classification over the existing analysis/attempt ledgers.

No provider calls, enqueueing, status resets, formal records or implicit retries.
Only a validated saved response can be proposed for a free local commit.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import json

from ..ai_settings import AIConfig
from ..extraction_errors import ExtractionFailure
from ..personal_store import encode
from ..source_metadata import snapshot_with_metadata
from .analysis import build_messages, validate_output


def dispatch_hold(connection, job, at):
    """A stale backlog or a received/unknown response needs deliberate recovery."""
    batch = connection.execute('SELECT * FROM wechat_batch WHERE id=?', (job['batch_id'],)).fetchone()
    review = connection.execute('SELECT * FROM raw_review WHERE raw_snapshot_id=?', (job['snapshot_id'],)).fetchone()
    if (not batch or batch['superseded_by'] or batch['status'] in {'ignored', 'superseded', 'archived'}
            or not review or review['status'] not in {'pending','archived'} or review['extraction_id'] != job['extraction_id']):
        return 'inactive_or_replaced'
    attempts = connection.execute('SELECT * FROM wechat_llm_attempt WHERE job_id=?', (job['id'],)).fetchall()
    if any(a['outcome_unknown'] or a['status'] == 'running' for a in attempts):
        return 'outcome_unknown'
    if any(a['kind'] == 'text' and (a['status'] == 'success' or a['raw_output'])
           or a['kind'] == 'vision' and a['status'] != 'success' and a['raw_output'] for a in attempts):
        return 'received_response_requires_local_review'
    scan = connection.execute('SELECT stats_json FROM wechat_scan WHERE id=?', (batch['scan_id'],)).fetchone()
    if scan and json.loads(scan['stats_json']).get('recovery_requires_approval'):
        return 'catch_up_requires_approval'
    captured = connection.execute("SELECT MAX(m.captured_at) FROM wechat_batch_message bm JOIN wechat_message m ON m.id=bm.message_id WHERE bm.batch_id=? AND bm.role='primary'", (job['batch_id'],)).fetchone()[0]
    if captured and at - datetime.fromisoformat(captured) >= timedelta(hours=24):
        return 'historical_source_requires_approval'
    if at - datetime.fromisoformat(job['created_at']) >= timedelta(hours=24):
        return 'historical_recovery_requires_approval'
    return None


def eligible_job_ids(connection, jobs, *, at, limit):
    """Select bounded runnable work without letting held history occupy the head.

    This is read-only. run_one/reserve must still recheck immediately before a
    provider request, since the review or source can change after selection.
    """
    selected = []
    for job in jobs:
        if job['next_attempt_at'] and job['status'] != 'budget_deferred' and at < datetime.fromisoformat(job['next_attempt_at']):
            continue
        if not dispatch_hold(connection, job, at):
            selected.append(job['id'])
            if len(selected) >= limit:
                break
    return selected


def backlog_plan(personal, *, at):
    rows = []
    with personal.connection() as c:
        jobs = c.execute("SELECT j.*,b.status AS batch_status,b.superseded_by,b.raw_item_id,ch.enabled,r.status AS review_status,r.extraction_id AS current_extraction FROM wechat_analysis_job j JOIN wechat_batch b ON b.id=j.batch_id JOIN wechat_chat ch ON ch.id=b.chat_id JOIN raw_review r ON r.raw_snapshot_id=j.snapshot_id WHERE j.status NOT IN ('parsed','filtered','superseded') ORDER BY j.created_at,j.id").fetchall()
        for row in jobs:
            job = dict(row)
            attempts = c.execute('SELECT * FROM wechat_llm_attempt WHERE job_id=? ORDER BY attempt_number', (job['id'],)).fetchall()
            item = {k: job[k] for k in ('id', 'batch_id', 'snapshot_id', 'raw_item_id', 'status', 'error_code', 'batch_status', 'review_status')}
            item['attempts'] = [{k: a[k] for k in ('id', 'kind', 'status', 'outcome_unknown', 'reserved_tokens', 'started_at')} for a in attempts]
            item['hold'] = dispatch_hold(c, job, at)
            item['primary_source_ids'] = [r[0] for r in c.execute("SELECT message_id FROM wechat_batch_message WHERE batch_id=? AND role='primary' ORDER BY ordinal", (job['batch_id'],))]
            active = (job['enabled'] and not job['superseded_by'] and job['batch_status'] not in {'ignored','superseded','archived'}
                      and job['review_status'] == 'pending' and job['current_extraction'] == job['extraction_id'])
            if not active:
                item['decision'] = 'skip_inactive_or_replaced'
            elif any(a['status'] == 'success' for a in attempts):
                item['decision'] = 'reconcile_success_without_http'
            elif any(a['outcome_unknown'] or a['status'] == 'running' for a in attempts):
                item['decision'] = 'hold_unknown_no_retry'
            else:
                batch = dict(c.execute('SELECT * FROM wechat_batch WHERE id=?', (job['batch_id'],)).fetchone())
                batch['primary_ids'] = item['primary_source_ids']
                snapshot = snapshot_with_metadata(c, c.execute('SELECT * FROM raw_snapshot WHERE id=?', (job['snapshot_id'],)).fetchone())
                latest = attempts[-1] if attempts else None
                item['saved_response_validation'] = None
                saved_raw = latest['raw_output'] if latest and latest['kind'] == 'text' else None
                recovery_path = personal.path.parent / 'wechat-recovery' / (job['id'] + '.json')
                if recovery_path.exists():
                    try:
                        from ..ai_settings import protect
                        stored = json.loads(recovery_path.read_text(encoding='utf-8'))
                        saved = json.loads(protect(stored['value'], decrypt=True) if stored['protected'] else stored['value'])
                        matches = all(saved.get('job', {}).get(k) == job[k] for k in ('id','snapshot_id','input_key','extraction_id'))
                        if not matches:
                            item['recovery_file'] = 'source_mismatch'
                        elif saved.get('kind') == 'vision':
                            item['recovery_file'] = 'vision_requires_review'
                        elif len(attempts) == 1 and latest['kind'] == 'text':
                            saved_raw = saved['raw']
                            item['recovery_file'] = 'matching_text_response'
                        else:
                            item['recovery_file'] = 'ambiguous_attempt'
                    except Exception:
                        item['recovery_file'] = 'protected_response_unavailable'
                if saved_raw:
                    try:
                        validate_output(saved_raw, batch, snapshot, json.loads(job['profile_json']), legacy=True)
                        item['saved_response_validation'] = 'passed'
                    except ExtractionFailure as exc:
                        item['saved_response_validation'] = exc.code
                    except (ValueError, KeyError, TypeError):
                        item['saved_response_validation'] = 'invalid_saved_response'
                if item['saved_response_validation'] == 'passed':
                    item['decision'] = 'local_saved_response_only'
                elif attempts:
                    item['decision'] = 'hold_failed_requires_review'
                else:
                    item['decision'] = 'paid_request_requires_approval'
                config = AIConfig.model_validate(json.loads(job['config_json']))
                messages = build_messages(batch, snapshot, json.loads(job['profile_json']))
                item['model'] = config.model
                item['max_output_tokens'] = config.max_output_tokens
                # Match the existing conservative reservation, not a billing claim.
                item['reserved_tokens_if_requested'] = len(encode(messages).encode()) + config.max_output_tokens
            rows.append(item)
    paid = [r for r in rows if r['decision'] == 'paid_request_requires_approval']
    return {'dry_run': True, 'generated_at': at.isoformat(), 'jobs': rows,
            'decisions': dict(Counter(r['decision'] for r in rows)),
            'paid_plan': {'jobs': len(paid), 'http_requests': len(paid), 'vision_requests': 0,
                'reserved_tokens': sum(r['reserved_tokens_if_requested'] for r in paid),
                'max_output_tokens': sum(r['max_output_tokens'] for r in paid),
                'currency_budget': None, 'approval_required': True},
            'performed_http_requests': 0, 'formal_records_created': 0}
