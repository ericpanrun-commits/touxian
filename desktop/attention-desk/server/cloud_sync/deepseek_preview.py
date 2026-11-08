"""Read-only bridge from validated DeepSeek notices to existing text projections.

This constructs a review artifact, never a cloud permission or publication.
Personal action routing remains local: an optional school notice is not a task.
"""
from contextlib import contextmanager
import hashlib
import json
import re

from ..personal_store import encode
from ..wechat_collect.analysis import PROMPT_VERSION, primary_message_ids, snapshot_with_metadata
from ..wechat_collect.deepseek_content_gate import prepare_authorized_projection, minimize_notice
from .minimization import selected_text_preview
from .minimized_projection import minimized_projection_preview
from .wechat_outbound_validation import validate_outbound


class _ReadSnapshot:
    def __init__(self, connection):
        self.db=connection

    @contextmanager
    def connection(self, *, write=False, migration=False):
        if write or migration or not self.db.in_transaction:
            raise ValueError('read_snapshot_required')
        yield self.db


def _normalized(text):
    return re.sub(r'[\t \u2005\u200b]+',' ',text).strip()


def _spans(positions):
    result=[]
    for position in positions:
        if position<0:
            continue
        if result and result[-1]['end']==position:
            result[-1]['end']+=1
        else:
            result.append({'start':position,'end':position+1})
    return result


def build_deepseek_preview(connection, grant, job_id):
    """Use one caller-owned transaction, suitable for later protected rebuilds.

    Caller supplies a separately read DeepSeek grant only to recheck analyzed
    source scope. That grant cannot authorize any of the returned cloud records.
    The sender must revalidate under its source lock and obtain separate approval.
    """
    personal=_ReadSnapshot(connection)
    if not connection.in_transaction:
        raise ValueError('read_snapshot_required')
    job=connection.execute('SELECT * FROM wechat_analysis_job WHERE id=?',(job_id,)).fetchone()
    if not job or job['status']!='parsed':
        raise ValueError('completed_analysis_required')
    extraction=connection.execute('SELECT * FROM ai_extraction WHERE id=?',(job['extraction_id'],)).fetchone()
    if not extraction or extraction['prompt_version']!=PROMPT_VERSION:
        raise ValueError('current_analysis_required')
    attempts=connection.execute("SELECT * FROM wechat_llm_attempt WHERE job_id=? AND status='success' AND kind='text'",(job_id,)).fetchall()
    if len(attempts)!=1 or not attempts[0]['raw_output']:
        raise ValueError('unambiguous_success_receipt_required')
    batch=dict(connection.execute('SELECT * FROM wechat_batch WHERE id=?',(job['batch_id'],)).fetchone())
    if batch['superseded_by'] or batch['status'] in {'ignored','superseded'}:
        raise ValueError('current_source_required')
    batch['primary_ids']=primary_message_ids(connection,batch['id'])
    snapshot=snapshot_with_metadata(connection,connection.execute('SELECT * FROM raw_snapshot WHERE id=?',(job['snapshot_id'],)).fetchone())
    projection=prepare_authorized_projection(personal,batch,snapshot,grant)
    parsed,_=projection.restore(attempts[0]['raw_output'],json.loads(job['profile_json']))
    candidates={r['model_item_id']:dict(r) for r in connection.execute('SELECT * FROM wechat_candidate WHERE batch_id=?',(batch['id'],))}
    selected=[]
    for item in parsed['items']:
        saved=candidates.get(item['id'])
        if not saved or saved['status'] in {'ignored','accepted','context_resolved','superseded'}:
            continue
        if json.loads(saved['data_json'])!=item:
            raise ValueError('saved_analysis_changed')
        if (item['is_school_notice'] is True and item['relevant'] is not False
                and item['information_complete'] and not item['await_more_context']):
            selected.append(item)
    if not selected:
        raise ValueError('no_complete_school_notice')
    selected_ids={mid for item in selected for mid in item['evidence_message_ids']}
    names=[r[0] for r in connection.execute('SELECT DISTINCT sender FROM wechat_message WHERE sender IS NOT NULL')]
    messages=[];expected_spans={};expected_text=[]
    for message in projection.snapshot['wechat']['messages']:
        mid=projection.message_ids[message['message_id']]
        if mid not in selected_ids:
            continue
        _,original,positions=projection.blocks[message['block_id']]
        spans=_spans(positions)
        excerpts=[original[s['start']:s['end']] for s in spans]
        messages.append({'id':mid,'excerpts':excerpts})
        expected_spans[mid]=spans
        expected_text.append(message['raw_text'])
    if not messages or selected_ids!={m['id'] for m in messages}:
        raise ValueError('text_evidence_required')
    title=selected[0]['title'] if len(selected)==1 else '学校通知'
    if minimize_notice(title,names)!=title:
        raise ValueError('title_requires_review')
    selection={'raw_item_id':batch['raw_item_id'],'snapshot_id':job['snapshot_id'],
        'title':title,'reason':'已验证的 DeepSeek 学校通知；个人行动判断独立保留，云端传输尚未授权。',
        'messages':messages}
    preview=selected_text_preview(personal,[selection])
    sample=preview['samples'][0]
    for evidence in sample['local_evidence']:
        if evidence['local_excerpt_spans']!=expected_spans[evidence['message_id']]:
            raise ValueError('cloud_excerpt_mapping_changed')
    # Existing minimization must not silently remove an audience or rewrite
    # qualifications. Only its known whitespace normalization is tolerated.
    if sample['payload']['text']!=_normalized('\n'.join(expected_text)) or sample['payload']['title']!=_normalized(title):
        raise ValueError('cloud_minimization_changed_meaning')
    bundle=minimized_projection_preview(personal,preview)
    raw=connection.execute('SELECT * FROM raw_item WHERE id=?',(batch['raw_item_id'],)).fetchone()
    review=connection.execute('SELECT * FROM raw_review WHERE raw_snapshot_id=?',(job['snapshot_id'],)).fetchone()
    # Local journal fingerprints are never included in the outgoing records.
    bundle['local_guards']=[{'raw_id':batch['raw_item_id'],'snapshot_id':job['snapshot_id'],
        'raw_guard':hashlib.sha256(encode(dict(raw)).encode()).hexdigest(),
        'review_guard':hashlib.sha256(encode(dict(review)).encode()).hexdigest()}]
    records={record['kind']:record for record in bundle['records']}
    validate_outbound(records,batch['raw_item_id'],job['snapshot_id'],records['raw_item']['payload']['source_id'])
    return {'preview_only':True,'upload_authorized':False,'job_id':job_id,
        'analysis_receipt_sha256':hashlib.sha256(encode([job_id,attempts[0]['id'],attempts[0]['raw_output']]).encode()).hexdigest(),
        'notice_count':len(selected),'mandatory_action_count':sum(bool(i['action_required']) for i in selected),
        'samples':preview,'bundle':bundle,'cloud_outbound_validation':'passed','requires_cloud_outbound_validation':True,
        'requires_separate_upload_authorization':True}
