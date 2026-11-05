"""Local review of selected source excerpts. No network, queue or domain writes."""
from __future__ import annotations

import hashlib
import re

from ..personal_store import encode

POLICY='selected-text-review-v1'
SENSITIVE=re.compile(r'身份证|银行卡|病历|诊断|医保|家庭住址|政治面貌|党籍|心理咨询|贫困生|处分名单')
PII=(
    (r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[联系方式已去除]'),
    (r'https?://[^\s<>]+', '[链接已去除]'),
    (r'(?<!\d)(?:\+?86[ -]*)?1[3-9](?:[ -]*\d){9}(?!\d)', '[号码已去除]'),
    (r'(?<!\d)\d{6,20}[Xx]?(?!\d)', '[号码已去除]'),
    (r'wxid_[A-Za-z0-9_]+', '[微信标识已去除]'),
    (r'@[^\s@，,：:。；;！!\n]{1,40}', '[提及已去除]'),
)


def redact_text(text, *, known_names=()):
    for pattern,replacement in PII:
        text=re.sub(pattern,replacement,text,flags=re.IGNORECASE)
    for name in sorted(set(known_names),key=len,reverse=True):
        if len(name)>=2:
            text=text.replace(name,'[姓名已去除]')
    return re.sub(r'[\t \u2005\u200b]+',' ',text).strip()


def selected_text_preview(personal, selections):
    """Only caller-reviewed literal excerpts may enter a proposal.

    Stable original IDs are retained. No original snapshot, sender directory,
    attachments, OCR, inferred course assignment or aggregate view is exported.
    This result is a review artifact and cannot authorize worker publication.
    """
    samples=[]
    with personal.connection() as c:
        names=[r[0] for r in c.execute("SELECT DISTINCT sender FROM wechat_message WHERE length(sender) BETWEEN 2 AND 40 AND sender NOT LIKE 'wxid_%'")]
        for selection in selections:
            raw=c.execute('SELECT * FROM raw_item WHERE id=?',(selection['raw_item_id'],)).fetchone()
            if not raw:raise ValueError('raw_not_found')
            snapshot=c.execute('SELECT raw_item_id FROM raw_snapshot WHERE id=?',(selection['snapshot_id'],)).fetchone()
            if not snapshot or snapshot['raw_item_id']!=raw['id']:raise ValueError('snapshot_scope_mismatch')
            evidence=[];texts=[];all_excerpts=[]
            for source in selection['messages']:
                message=c.execute("SELECT m.*,ch.source_id FROM wechat_message m JOIN wechat_chat ch ON ch.id=m.chat_id JOIN wechat_batch_message bm ON bm.message_id=m.id JOIN wechat_batch b ON b.id=bm.batch_id WHERE m.id=? AND b.raw_item_id=? AND bm.role='primary' LIMIT 1",(source['id'],raw['id'])).fetchone()
                if not message or message['message_type']!='text':raise ValueError('text_source_scope_mismatch')
                if not c.execute('SELECT 1 FROM wechat_snapshot_message WHERE snapshot_id=? AND message_id=?',
                        (selection['snapshot_id'],message['id'])).fetchone():
                    raise ValueError('message_not_in_selected_snapshot')
                spans=[]
                for excerpt in source['excerpts']:
                    start=message['raw_text'].find(excerpt)
                    if not excerpt.strip() or start<0:raise ValueError('excerpt_not_in_original_message')
                    spans.append({'start':start,'end':start+len(excerpt)})
                    texts.append(redact_text(excerpt,known_names=names));all_excerpts.append(excerpt)
                if not spans:raise ValueError('empty_source_excerpt')
                evidence.append({'source_id':message['source_id'],'message_id':message['id'],
                    'published_at':message['source_time'],'time_reliability':message['time_reliability'],
                    'local_evidence_sha256':hashlib.sha256(message['raw_text'].encode()).hexdigest(),
                    'local_excerpt_spans':spans})
            if not texts:raise ValueError('empty_selection')
            source_ids={row['source_id'] for row in evidence}
            if len(source_ids)!=1:raise ValueError('mixed_source_raw')
            course=c.execute('SELECT id,name FROM course WHERE id=?',(raw['inbox_course_id'],)).fetchone() if raw['inbox_course_id'] else None
            text='\n'.join(texts)
            title=redact_text(selection['title'],known_names=names)
            sensitive=bool(SENSITIVE.search(text+'\n'+title))
            if any(quote not in '\n'.join(all_excerpts) for quote in selection.get('schedule_quotes',[])):
                raise ValueError('schedule_quote_not_in_selected_evidence')
            payload={'id':raw['id'],'source_id':next(iter(source_ids)),
                'source_snapshot_id':selection['snapshot_id'],
                'source_message_ids':[row['message_id'] for row in evidence],
                'published_at':[row['published_at'] for row in evidence],
                'publication_time_reliability':[row['time_reliability'] for row in evidence],
                'title':title,'text':text,'course':dict(course) if course else None,
                'course_link_basis':'existing_confirmed_link' if course else 'not_established',
                'schedule_quotes':[redact_text(quote,known_names=names) for quote in selection.get('schedule_quotes',[])],
                'attachments':[],'minimization_policy':POLICY,'redacted_excerpt':True}
            identity=encode([payload['source_id'],raw['id'],sorted(payload['source_message_ids'])])
            samples.append({'review_status':'sensitive_content_requires_exclusion' if sensitive else 'awaiting_user_review',
                'upload_authorized':False,'payload':payload,
                'dedupe_key':hashlib.sha256(identity.encode()).hexdigest(),
                'preview_hash':hashlib.sha256(encode(payload).encode()).hexdigest(),
                'local_evidence':evidence,'selection_reason':selection['reason']})
    return {'preview_only':True,'upload_authorized':False,'model_requests':0,'attachment_count':0,
        'selected_raw_count':len(samples),'proposed_raw_count':sum(s['review_status']=='awaiting_user_review' for s in samples),
        'planned_projection_kinds':['raw_item','inbox_item','inbox','raw_review'],
        'planned_record_count':4*sum(s['review_status']=='awaiting_user_review' for s in samples),
        'requires_scope_filter_before_any_publish':True,'samples':samples}
