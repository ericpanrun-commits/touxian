"""Read-only backlog classification over the existing analysis/attempt ledgers.

No provider calls, enqueueing, status resets, formal records or implicit retries.
Only a validated saved response can be proposed for a free local commit.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import re

from ..ai_settings import AIConfig
from ..extraction_errors import ExtractionFailure
from ..personal_store import encode
from ..source_metadata import snapshot_with_metadata
from .analysis import build_text_request, primary_message_ids, validate_output
from ..recovery_controls import paid_ai_paused


def dispatch_hold(connection, job, at, *, automatic=None):
    """A stale backlog or a received/unknown response needs deliberate recovery."""
    if paid_ai_paused():
        return 'paid_analysis_paused'
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
    scoped=False
    if automatic is not None:
        try:automatic.check(permission='analyze',at=at);scoped=automatic.contains(connection,batch)
        except (OSError,ValueError,KeyError):return 'automation_changed'
        if not scoped:return 'outside_automation_window'
    if scan and json.loads(scan['stats_json']).get('recovery_requires_approval') and not scoped:
        return 'catch_up_requires_approval'
    captured = connection.execute("SELECT MAX(m.captured_at) FROM wechat_batch_message bm JOIN wechat_message m ON m.id=bm.message_id WHERE bm.batch_id=? AND bm.role='primary'", (job['batch_id'],)).fetchone()[0]
    if captured and at - datetime.fromisoformat(captured) >= timedelta(hours=24) and not scoped:
        return 'historical_source_requires_approval'
    if at - datetime.fromisoformat(job['created_at']) >= timedelta(hours=24) and not scoped:
        return 'historical_recovery_requires_approval'
    return None


def eligible_job_ids(connection, jobs, *, at, limit, automatic=None):
    """Select bounded runnable work without letting held history occupy the head.

    This is read-only. run_one/reserve must still recheck immediately before a
    provider request, since the review or source can change after selection.
    """
    selected = []
    for job in jobs:
        if job['next_attempt_at'] and job['status'] != 'budget_deferred' and at < datetime.fromisoformat(job['next_attempt_at']):
            continue
        if not dispatch_hold(connection, job, at,automatic=automatic):
            selected.append(job['id'])
            if len(selected) >= limit:
                break
    return selected


def request_plan(personal, batch, config, messages, *, allow_vision=False, authorized_text=False):
    """Read-only cost stages, matching production's per-batch vision permission.

    A successful vision response changes the text snapshot. Its later text
    reservation cannot be presented as known before that response exists.
    """
    from .vision import pending_media, vision_body
    with personal.connection() as c:
        table=c.execute("SELECT 1 FROM sqlite_master WHERE name='wechat_media_analysis'").fetchone()
        recovered=c.execute('SELECT vision_allowed FROM wechat_media_analysis WHERE batch_id=?',(batch['id'],)).fetchone() if table else None
    media=pending_media(personal,batch['id']) if not authorized_text and (allow_vision or recovered and recovered[0]) else []
    current_text=len(encode(messages).encode())+config.max_output_tokens
    result={'text_requests_max':1,'vision_requests_max':int(bool(media)),
        'http_requests_max':1+int(bool(media)),'vision_images':len(media),
        'current_text_reservation':current_text,'initial_reserved_tokens':current_text,
        'post_vision_text_reservation_unknown':bool(media),'vision_preflight_error':None}
    if media:
        result['initial_reserved_tokens']=None
        try:
            body=vision_body(media,config,personal.path.parent/'files')
            text_parts=[part for message in body['messages'] for part in message['content'] if part.get('type')=='text']
            result['initial_reserved_tokens']=len(encode(text_parts).encode())+len(media)*4096+config.max_output_tokens
        except ExtractionFailure as exc:
            result['vision_preflight_error']=exc.code
        except OSError:
            result['vision_preflight_error']='vision_original_unavailable'
    return result


def text_disclosure(batch, snapshot, context, body):
    """Describe possible disclosure without saving messages or profile values."""
    messages = snapshot['wechat']['messages']
    blocks = snapshot['blocks']
    text = '\n'.join(str(block.get('text') or '') for block in blocks)
    patterns = {'phone': r'(?<!\d)(?:\+?86[ -]*)?1[3-9](?:[ -]*\d){9}(?!\d)',
        'email': r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
        'url': r'https?://[^\s<>]+', 'wechat_identifier': r'wxid_[A-Za-z0-9_]+'}
    primary = set(batch['primary_ids'])
    return {'request_sha256': hashlib.sha256(encode(body).encode()).hexdigest(),
        'request_hash_scope': 'canonical_json_preview_not_authorization',
        'message_types': dict(Counter(message.get('message_type') or 'unknown' for message in messages)),
        'block_source_types': dict(Counter(block.get('source_type') or 'unknown' for block in blocks)),
        'context_message_count': sum(message['message_id'] not in primary for message in messages),
        'sender_fields_nonempty': sum(bool(message.get('sender')) for message in messages),
        'user_context_fields_present': sorted(key for key, value in context.items() if value not in (None, '', [], {})),
        'indicators': {key: bool(re.search(pattern, text, re.IGNORECASE)) for key, pattern in patterns.items()},
        'indicators_are_lower_bounds': True, 'requires_content_review': True}


def backlog_plan(personal, *, at, allow_vision=False):
    rows = []
    with personal.connection() as c:
        jobs = c.execute("SELECT j.*,b.status AS batch_status,b.superseded_by,b.raw_item_id,ch.enabled,r.status AS review_status,r.extraction_id AS current_extraction FROM wechat_analysis_job j JOIN wechat_batch b ON b.id=j.batch_id JOIN wechat_chat ch ON ch.id=b.chat_id JOIN raw_review r ON r.raw_snapshot_id=j.snapshot_id WHERE j.status NOT IN ('parsed','filtered','superseded') ORDER BY j.created_at,j.id").fetchall()
        for row in jobs:
            job = dict(row)
            attempts = c.execute('SELECT * FROM wechat_llm_attempt WHERE job_id=? ORDER BY attempt_number', (job['id'],)).fetchall()
            item = {k: job[k] for k in ('id', 'batch_id', 'snapshot_id', 'raw_item_id', 'status', 'error_code', 'batch_status', 'review_status')}
            item['attempts'] = [{k: a[k] for k in ('id', 'kind', 'status', 'outcome_unknown', 'reserved_tokens', 'started_at')} for a in attempts]
            item['has_unknown_attempt'] = any(a['outcome_unknown'] or a['status'] in
                {'running', 'outcome_unknown', 'timeout', 'connection_error', 'storage_or_response_error'} for a in attempts)
            item['hold'] = dispatch_hold(c, job, at)
            item['primary_source_ids'] = primary_message_ids(c, job['batch_id'])
            active = (job['enabled'] and not job['superseded_by'] and job['batch_status'] not in {'ignored','superseded','archived'}
                      and job['review_status'] == 'pending' and job['current_extraction'] == job['extraction_id'])
            if not active:
                item['decision'] = 'skip_inactive_or_replaced'
            elif item['has_unknown_attempt']:
                item['decision'] = 'hold_unknown_no_retry'
            elif any(a['kind'] == 'text' and a['status'] == 'success' for a in attempts):
                item['decision'] = 'reconcile_success_without_http'
            else:
                batch = dict(c.execute('SELECT * FROM wechat_batch WHERE id=?', (job['batch_id'],)).fetchone())
                batch['primary_ids'] = item['primary_source_ids']
                snapshot = snapshot_with_metadata(c, c.execute('SELECT * FROM raw_snapshot WHERE id=?', (job['snapshot_id'],)).fetchone())
                latest = attempts[-1] if attempts else None
                item['saved_response_validation'] = None
                saved_raw = latest['raw_output'] if latest and latest['kind'] == 'text' else None
                saved_projection = None
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
                            saved_projection = saved.get('projection')
                            item['recovery_file'] = 'matching_text_response'
                        else:
                            item['recovery_file'] = 'ambiguous_attempt'
                    except Exception:
                        item['recovery_file'] = 'protected_response_unavailable'
                if saved_raw:
                    try:
                        if saved_projection:
                            from .deepseek_content_gate import restore_record
                            restore_record(saved_projection,batch,snapshot,saved_raw,json.loads(job['profile_json']))
                        else:
                            validate_output(saved_raw, batch, snapshot, json.loads(job['profile_json']), legacy=True)
                        item['saved_response_validation'] = 'passed'
                    except ExtractionFailure as exc:
                        item['saved_response_validation'] = exc.code
                    except (ValueError, KeyError, TypeError):
                        item['saved_response_validation'] = 'invalid_saved_response'
                if item['saved_response_validation'] == 'passed':
                    item['decision'] = 'local_saved_response_only'
                elif attempts and all(a['kind'] == 'vision' and a['status'] == 'success' for a in attempts):
                    # A completed image transcription is not a completed text
                    # analysis. Review the remaining, potentially paid stage.
                    item['decision'] = 'hold_completed_vision_requires_review'
                elif attempts:
                    item['decision'] = 'hold_failed_requires_review'
                else:
                    item['decision'] = 'paid_request_requires_approval'
                config = AIConfig.model_validate(json.loads(job['config_json']))
                item['model'] = config.model
                item['max_output_tokens'] = config.max_output_tokens
                if item['decision'] == 'hold_completed_vision_requires_review':
                    # Do not propose replaying a successful vision request or
                    # estimate text before its saved output has been reviewed.
                    item['request_plan'] = None
                    item['reserved_tokens_if_requested'] = None
                else:
                    context = json.loads(job['profile_json'])
                    body = build_text_request(config, batch, snapshot, context)
                    sent_batch,sent_snapshot,sent_context=batch,snapshot,context
                    authorized_text=False
                    try:
                        from .deepseek_content_gate import leased_grant,prepare_authorized_projection
                        with leased_grant(personal) as grant:
                            projection=prepare_authorized_projection(personal,batch,snapshot,grant)
                        sent_batch,sent_snapshot,sent_context=projection.batch,projection.snapshot,projection.context
                        body=build_text_request(config,sent_batch,sent_snapshot,sent_context)
                        item['content_gate']='approved_text_projection'
                        authorized_text=True
                    except ExtractionFailure as exc:
                        item['content_gate']=exc.code
                    item['request_plan']=request_plan(personal,batch,config,body['messages'],allow_vision=allow_vision,authorized_text=authorized_text)
                    if item['decision'] == 'paid_request_requires_approval':
                        item['text_disclosure'] = text_disclosure(sent_batch, sent_snapshot, sent_context, body)
                        item['text_disclosure']['may_change_after_vision'] = item['request_plan']['post_vision_text_reservation_unknown']
                    # This is a reservation estimate, never a price or an authorization.
                    item['reserved_tokens_if_requested'] = (None if item['request_plan']['post_vision_text_reservation_unknown']
                        or item['request_plan']['vision_preflight_error'] else item['request_plan']['initial_reserved_tokens'])
            rows.append(item)
    paid = [r for r in rows if r['decision'] == 'paid_request_requires_approval']
    vision=sum(r['request_plan']['vision_requests_max'] for r in paid)
    unknown=sum(r['request_plan']['post_vision_text_reservation_unknown'] or bool(r['request_plan']['vision_preflight_error']) for r in paid)
    return {'dry_run': True, 'generated_at': at.isoformat(), 'jobs': rows,
            'decisions': dict(Counter(r['decision'] for r in rows)),
            'unknown_attempt_jobs': sum(r['has_unknown_attempt'] for r in rows),
            'paid_plan': {'jobs': len(paid), 'http_requests': len(paid)+vision,
                'text_requests':len(paid),'vision_requests':vision,
                'request_count_basis':'upper_bound_before_shared_vision_cache_reuse',
                'reserved_tokens': None if unknown else sum(r['reserved_tokens_if_requested'] for r in paid),
                'known_initial_reserved_tokens':sum(r['request_plan']['initial_reserved_tokens'] or 0 for r in paid),
                'jobs_requiring_post_vision_budget_check':vision,
                'vision_preflight_errors':sum(bool(r['request_plan']['vision_preflight_error']) for r in paid),
                'max_output_tokens': sum(r['max_output_tokens']*r['request_plan']['http_requests_max'] for r in paid),
                'currency_budget': None, 'approval_required': True},
            'performed_http_requests': 0, 'formal_records_created': 0}
