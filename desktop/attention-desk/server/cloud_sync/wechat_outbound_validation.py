"""Strict validation of the existing four text-only WeChat read models.

Validation does not grant consent or prove complete redaction. The protected
sender must separately recheck source evidence and the cloud content permission.
"""
from datetime import datetime
import hashlib
import json
import re
import unicodedata
from ..wechat_collect.qr_text_policy import document_warnings, scan_notice


RAW='id source_id kind source_type created_at title published_at inbox_original_snapshot_id inbox_snapshot_id inbox_course_id inbox_version redacted_excerpt minimization_policy'.split()
SUMMARY='id created_at source source_kind input_kind title preview preview_source vision_notice ocr_preview processing_status processing_stage original_snapshot_id snapshot_id course project_ref classification error proposal_count review_status version attachments managed needs_manual redacted_excerpt'.split()
DETAIL=SUMMARY+'original review_available review extraction_quality school_documents receipts'.split()
REVIEW='raw_snapshot_id title_hint status version created_at updated_at snapshot item source extraction_id draft extraction confirmed_event_ids wechat_candidates course_suggestions source_update'.split()
SNAPSHOT='id raw_item_id original_text created_at source_time source_timezone hash blocks attachments attachment_jobs document_warnings redacted_excerpt minimization_policy wechat'.split()
WECHAT='source batch_id messages warnings analysis superseded_by latest_snapshot_id'.split()
BLOCK='raw_snapshot_id block_id source_type ordinal text section needs_review attachment_id'.split()
MESSAGE='snapshot_id block_id message_id content_revision raw_text message_type source_time time_reliability captured_at'.split()
POLICY='selected-text-review-v1'
UUID=re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
STAMP=re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$')
AMBIGUOUS=re.compile(r'&(?:#(?:x[0-9a-f]{1,6}|[0-9]{1,7});?|[a-z][a-z0-9]{1,31};)|%[0-9a-f]{2}|\\u[0-9a-f]{4}',re.I)
EMAIL=re.compile(r'[a-z0-9._%+-]+[ \t]*@[ \t]*[a-z0-9-]+(?:[ \t]*\.[ \t]*[a-z0-9-]+)+',re.I)
PHONE=re.compile(r'(?<!\d)(?:\+?86[ \t.()\-]*)?1[ \t.()\-]*[3-9](?:[ \t.()\-]*\d){9}(?!\d)')
MENTION=re.compile(r'@[^\s@，,：:。；;！!\n]{1,40}')
SENSITIVE=re.compile(r'https?://|wxid_[a-z0-9_]+|(?<!\d)\d{6,}[x]?(?!\d)|(?:[a-z]:\\|\\\\|/users/|/home/)|姓名|学号|身份证|银行卡|病历|诊断|医保|家庭住址|政治面貌|心理咨询|贫困生|处分名单|接龙|名单|联系人|负责老师',re.I)
NAMED=re.compile(r'[\u4e00-\u9fff]{2,4}(?:同学|老师|教授)')
GENERIC_POLICY=re.compile(r'^\s*(?:[0-9]{1,2}[.、]\s*)?(?:[!！❗\ufe0f]\s*)*'
    r'(?:报名前需确认好本人空闲时间[，,]\s*)?(?:报名成功后不得随意更改[，,]\s*)?'
    r'(?:随意取消活动|无故缺席|违规报名)(?:将会|将|会)(?:被)?(?:拉入|列入)黑名单[。；;！!]?\s*$')
GENERIC_SHORT={'通知','注意事项','温馨提示','大家好','晚上好','上午好','下午好','谢谢','谢谢配合','工作时间','工作地点','服装要求','报名方式'}


def _privacy_scan_text(text):
    # Recognize only a complete, bounded vocabulary for an impersonal rule.
    # Never strip the word "名单" generally or alter the outgoing source text.
    lines=re.split(r'\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]',text)
    matched=[bool(GENERIC_POLICY.fullmatch(line)) for line in lines]
    if any(matched):
        for line,is_policy in zip(lines,matched):
            short=re.fullmatch(r'\s*(?:[0-9]{1,2}[.、]\s*)?([\u4e00-\u9fff]{2,4})[。；;！!]?\s*',line)
            if not is_policy and short and short[1] not in GENERIC_SHORT:
                return None  # A policy sentence does not authorize a nearby name.
    return '\n'.join('通用报名纪律要求' if matched[i] else line for i,line in enumerate(lines))


def _require(condition):
    if not condition:raise ValueError('invalid_outbound_projection')


def _shape(value,fields):
    return isinstance(value,dict) and set(value)==set(fields)


def _same(left,right):
    return json.dumps(left,ensure_ascii=False,sort_keys=True,allow_nan=False)==json.dumps(right,ensure_ascii=False,sort_keys=True,allow_nan=False)


def _integer(value,minimum=0):
    return type(value) is int and minimum<=value<=9007199254740991


def _stamp(value):
    if not isinstance(value,str) or not STAMP.fullmatch(value):return False
    try:
        return datetime.fromisoformat(value.replace('Z','+00:00')).utcoffset() is not None
    except ValueError:
        return False


def _uuid(value):
    return isinstance(value,str) and UUID.fullmatch(value) is not None


def _safe_text(value, *, scan_audience=False):
    if not isinstance(value,str):return False
    try:
        if len(value.encode('utf-16-le'))//2>16000:return False
    except UnicodeEncodeError:
        return False
    if AMBIGUOUS.search(value):return False
    canonical=''.join(char for char in unicodedata.normalize('NFKC',value.replace('[姓名已去除]',''))
                      if unicodedata.category(char)!='Cf')
    if '[敏感内容待核对]' in canonical:return False
    canonical=_privacy_scan_text(canonical)
    if canonical is None:return False
    if scan_audience:canonical=canonical.replace('有意参加的同学','')
    unnamed=re.sub(r'各位同学|全体同学|全班同学|每位同学|所有同学|同学们|老师们','',canonical)
    return not (EMAIL.search(canonical) or PHONE.search(canonical) or MENTION.search(canonical) or
                SENSITIVE.search(canonical) or NAMED.search(unnamed))


def validate_outbound(records,raw_id,snapshot_id,source_id):
    """Validate all data and metadata before any HTTP call; raise on mismatch."""
    _require(isinstance(records,dict) and set(records)=={'raw_item','inbox_item','inbox','raw_review'})
    raw,summary,detail,review=(records[k]['payload'] for k in ('raw_item','inbox_item','inbox','raw_review'))
    _require(_shape(raw,RAW) and _shape(summary,SUMMARY) and _shape(detail,DETAIL) and _shape(review,REVIEW))
    scan_audience=scan_notice(review.get('snapshot',{}).get('wechat',{}).get('messages',[]))
    safe_text=lambda value:_safe_text(value,scan_audience=scan_audience)
    _require(raw['id']==raw_id and raw['source_id']==source_id and raw['kind']=='wechat_batch' and
        raw['source_type']=='wechat_batch' and raw['inbox_snapshot_id']==snapshot_id and
        raw['inbox_original_snapshot_id']==snapshot_id and raw['inbox_course_id'] is None and
        raw['redacted_excerpt'] is True and raw['minimization_policy']==POLICY and
        _integer(raw['inbox_version'],1) and _stamp(raw['created_at']) and
        safe_text(raw['title']) and raw['title'].startswith('【脱敏节选】'))
    _require(summary['id']==raw_id and summary['created_at']==raw['created_at'] and
        summary['source']=='微信通知（脱敏节选）' and summary['source_kind']=='wechat' and
        summary['input_kind']=='text' and summary['title']==raw['title'] and safe_text(summary['preview']) and
        summary['preview_source']=='text' and summary['original_snapshot_id']==snapshot_id and
        summary['snapshot_id']==snapshot_id and summary['redacted_excerpt'] is True and
        summary['needs_manual'] is True and summary['managed'] is False and
        summary['processing_status']=='needs_review' and summary['processing_stage']=='review' and
        summary['review_status'] in {'pending','archived'} and all(summary[k] is None for k in
            ('vision_notice','ocr_preview','project_ref','classification','error','course')) and
        type(summary['proposal_count']) is int and summary['proposal_count']==0 and summary['attachments']==[] and
        _integer(summary['version'],1) and summary['version']==raw['inbox_version'])
    _require(all(_same(summary[key],detail[key]) for key in SUMMARY) and detail['review_available'] is True and
        detail['school_documents']==[] and detail['receipts']==[] and
        _same(detail['extraction_quality'],{'files':[],'needs_review':True,'reasons':['redacted_excerpt'],'meaning':'脱敏文字节选'}))
    _require(review['raw_snapshot_id']==snapshot_id and review['title_hint']==raw['title'] and
        review['status']==summary['review_status'] and _integer(review['version'],1) and
        _stamp(review['created_at']) and _stamp(review['updated_at']) and
        all(review[k] is None for k in ('extraction_id','draft','extraction')) and
        review['confirmed_event_ids']==[] and review['wechat_candidates']==[] and review['course_suggestions']=={} and
        _same(review['source_update'],{'events':[],'proposals':[],'requires_confirmation':False}) and
        _same(review['source'],{'id':source_id,'kind':'wechat','name':'微信通知（脱敏节选）'}) and
        _same(review['item'],raw) and _same(detail['review'],review))
    snapshot=review['snapshot']
    _require(_shape(snapshot,SNAPSHOT) and _same(detail['original'],snapshot) and snapshot['id']==snapshot_id and
        snapshot['raw_item_id']==raw_id and snapshot['redacted_excerpt'] is True and
        snapshot['minimization_policy']==POLICY and _stamp(snapshot['created_at']) and
        snapshot['source_timezone'] in ('Asia/Shanghai','UTC','Etc/UTC') and
        safe_text(snapshot['original_text']) and
        snapshot['hash']==hashlib.sha256(snapshot['original_text'].encode('utf-8')).hexdigest() and
        snapshot['attachments']==[] and snapshot['attachment_jobs']==[] and
        snapshot['document_warnings']==document_warnings(snapshot.get('wechat',{}).get('messages',[])))
    wx=snapshot['wechat']
    _require(_shape(wx,WECHAT) and wx['source']=='wechat' and _uuid(wx['batch_id']) and wx['analysis']==[] and
        wx['warnings']==['redacted_excerpt'] and wx['superseded_by'] is None and wx['latest_snapshot_id']==snapshot_id and
        isinstance(wx['messages'],list) and 1<=len(wx['messages'])<=100 and
        isinstance(snapshot['blocks'],list) and len(snapshot['blocks'])==len(wx['messages']))
    seen=set()
    for message,block in zip(wx['messages'],snapshot['blocks']):
        _require(_shape(message,MESSAGE) and _shape(block,BLOCK))
        mid=message['message_id']
        _require(_uuid(mid) and mid not in seen and message['snapshot_id']==snapshot_id and
            block['raw_snapshot_id']==snapshot_id and message['block_id']=='WX_'+mid and
            block['block_id']==message['block_id'] and message['message_type']=='text' and
            block['source_type']=='text' and block['section']=='脱敏节选' and block['needs_review'] is True and
            block['attachment_id'] is None and _integer(block['ordinal']) and _integer(message['content_revision']) and
            message['time_reliability']=='reliable' and _stamp(message['source_time']) and _stamp(message['captured_at']) and
            safe_text(message['raw_text']) and bool(message['raw_text'].strip()) and block['text']==message['raw_text'])
        seen.add(mid)
    _require(snapshot['original_text']=='\n'.join(message['raw_text'] for message in wx['messages']) and
        summary['preview']==snapshot['original_text'][:160] and raw['published_at']==wx['messages'][0]['source_time'] and
        snapshot['source_time']==wx['messages'][0]['source_time'])
