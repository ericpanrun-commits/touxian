"""Build existing Eric OS read models for a local, unapproved text-only batch."""
from __future__ import annotations

import hashlib

from ..personal_store import encode
from .minimization import POLICY,selected_text_preview,redact_text
from .projection import record


def _fresh_review(personal, preview):
    if preview.get('preview_only') is not True or preview.get('upload_authorized') is not False:
        raise ValueError('not_an_unapproved_preview')
    selections=[]
    with personal.connection() as c:
        for sample in preview['samples']:
            if sample.get('review_status')!='awaiting_user_review' or sample.get('upload_authorized') is not False:
                raise ValueError('sample_not_ready_for_review')
            value=sample['payload'];messages=[]
            for evidence in sample['local_evidence']:
                row=c.execute('SELECT raw_text FROM wechat_message WHERE id=?',(evidence['message_id'],)).fetchone()
                if not row or hashlib.sha256(row['raw_text'].encode()).hexdigest()!=evidence['local_evidence_sha256']:
                    raise ValueError('original_evidence_changed')
                excerpts=[]
                for span in evidence['local_excerpt_spans']:
                    start,end=span['start'],span['end']
                    if type(start) is not int or type(end) is not int or not 0<=start<end<=len(row['raw_text']):
                        raise ValueError('invalid_excerpt_span')
                    excerpts.append(row['raw_text'][start:end])
                messages.append({'id':evidence['message_id'],'excerpts':excerpts})
            selections.append({'raw_item_id':value['id'],'snapshot_id':value['source_snapshot_id'],
                'title':value['title'],'reason':sample['selection_reason'],'schedule_quotes':value['schedule_quotes'],
                'messages':messages})
    fresh=selected_text_preview(personal,selections)
    expected=[(s['preview_hash'],s['local_evidence']) for s in fresh['samples']]
    supplied=[(s['preview_hash'],s['local_evidence']) for s in preview['samples']]
    if supplied!=expected or any(hashlib.sha256(encode(s['payload']).encode()).hexdigest()!=s['preview_hash'] for s in preview['samples']):
        raise ValueError('review_content_changed')
    return fresh


def minimized_projection_preview(personal, preview):
    """Explicit construction prevents hidden source/Review/Inbox fields escaping.

    Not connected to CloudSync.publish. A bundle hash binds all four projections
    to the reviewed text; it does not grant upload permission.
    """
    fresh=_fresh_review(personal,preview)
    ids=[s['payload']['id'] for s in fresh['samples']]
    if len(ids)!=len(set(ids)):raise ValueError('duplicate_raw_selection')
    records=[]
    with personal.connection() as c:
        names=[r[0] for r in c.execute("SELECT DISTINCT sender FROM wechat_message WHERE length(sender) BETWEEN 2 AND 40 AND sender NOT LIKE 'wxid_%'")]
        for sample in fresh['samples']:
            value=sample['payload'];raw_id=value['id'];sid=value['source_snapshot_id']
            raw=c.execute('SELECT * FROM raw_item WHERE id=?',(raw_id,)).fetchone()
            original=c.execute('SELECT * FROM raw_snapshot WHERE id=?',(sid,)).fetchone()
            owner=c.execute('SELECT * FROM raw_review WHERE raw_snapshot_id=?',(sid,)).fetchone()
            batch=c.execute('SELECT id FROM wechat_batch WHERE raw_item_id=? AND snapshot_id=?',(raw_id,sid)).fetchone()
            source=c.execute('SELECT kind FROM source WHERE id=?',(value['source_id'],)).fetchone()
            if not owner or not batch or not source:raise ValueError('existing_projection_owner_missing')
            title='【脱敏节选】'+value['title'];blocks=[];messages=[]
            for evidence in sample['local_evidence']:
                row=c.execute('SELECT * FROM wechat_message WHERE id=?',(evidence['message_id'],)).fetchone()
                mapping=c.execute('SELECT block_id,content_revision FROM wechat_snapshot_message WHERE snapshot_id=? AND message_id=? ORDER BY block_id LIMIT 1',(sid,row['id'])).fetchone()
                text='\n'.join(redact_text(row['raw_text'][span['start']:span['end']],known_names=names) for span in evidence['local_excerpt_spans'])
                blocks.append({'raw_snapshot_id':sid,'block_id':mapping['block_id'],'source_type':'text',
                    'ordinal':len(blocks)*100,'text':text,'section':'脱敏节选','needs_review':True,'attachment_id':None})
                messages.append({'snapshot_id':sid,'block_id':mapping['block_id'],'message_id':row['id'],
                    'content_revision':mapping['content_revision'],'raw_text':text,'message_type':'text',
                    'source_time':row['source_time'],'time_reliability':row['time_reliability'],
                    'captured_at':row['captured_at']})
            snapshot={'id':sid,'raw_item_id':raw_id,'original_text':value['text'],
                'created_at':original['created_at'],'source_time':value['published_at'][0],
                'source_timezone':original['source_timezone'],'hash':hashlib.sha256(value['text'].encode()).hexdigest(),
                'blocks':blocks,'attachments':[],'attachment_jobs':[],
                'document_warnings':[{'code':'redacted_excerpt','message':'这是经最小化的文字节选，完整原文仅保留在本机。'}],
                'redacted_excerpt':True,'minimization_policy':POLICY,
                'wechat':{'source':'wechat','batch_id':batch['id'],'messages':messages,
                    'warnings':['redacted_excerpt'],'analysis':[],'superseded_by':None,'latest_snapshot_id':sid}}
            item={'id':raw_id,'source_id':value['source_id'],'kind':raw['kind'],'source_type':raw['kind'],
                'created_at':raw['created_at'],'title':title,'published_at':value['published_at'][0],
                'inbox_original_snapshot_id':sid,'inbox_snapshot_id':sid,
                'inbox_course_id':value['course']['id'] if value['course'] else None,
                'inbox_version':raw['inbox_version'],'redacted_excerpt':True,'minimization_policy':POLICY}
            review={'raw_snapshot_id':sid,'title_hint':title,'status':owner['status'],'version':owner['version'],
                'created_at':owner['created_at'],'updated_at':owner['updated_at'],'snapshot':snapshot,
                'item':item,'source':{'id':value['source_id'],'kind':source['kind'],'name':'微信通知（脱敏节选）'},
                'extraction_id':None,'draft':None,'extraction':None,'confirmed_event_ids':[],
                'wechat_candidates':[],'course_suggestions':{},
                'source_update':{'events':[],'proposals':[],'requires_confirmation':False}}
            summary={'id':raw_id,'created_at':raw['created_at'],'source':'微信通知（脱敏节选）','source_kind':source['kind'],
                'input_kind':'text','title':title,'preview':value['text'][:160],'preview_source':'text',
                'vision_notice':None,'ocr_preview':None,'processing_status':'needs_review','processing_stage':'review',
                'original_snapshot_id':sid,'snapshot_id':sid,'course':value['course'],'project_ref':None,
                'classification':None,'error':None,'proposal_count':0,'review_status':owner['status'],
                'version':raw['inbox_version'],'attachments':[],'managed':False,'needs_manual':True,'redacted_excerpt':True}
            detail={**summary,'original':snapshot,'review_available':True,'review':review,
                'extraction_quality':{'files':[],'needs_review':True,'reasons':['redacted_excerpt'],'meaning':'脱敏文字节选'},
                'school_documents':[],'receipts':[]}
            records.extend((record('raw_item',raw_id,item),record('inbox_item',raw_id,summary),
                record('inbox',raw_id,detail),record('raw_review',sid,review)))
    return {'preview_only':True,'upload_authorized':False,'policy':POLICY,'resources':[],
        'raw_item_ids':ids,'record_count':len(records),'records':records,
        'review_preview_hashes':[s['preview_hash'] for s in fresh['samples']],
        'bundle_hash':hashlib.sha256(encode(records).encode()).hexdigest(),
        'requires_separate_upload_authorization':True}
