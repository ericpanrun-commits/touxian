"""Source-backed attention routing; no DB adapter, model request or formal write."""
from dataclasses import asdict, dataclass
from datetime import datetime,timedelta,timezone
import hashlib
import json
import re

from ..personal_store import encode,now

VERSION='attention-4'
QUESTION=re.compile(r'是不是|是否改|有没有|请问|好像|听说|据说|貌似|不确定|未确认|可能.{0,8}(?:改|换|在)|我记得|[?？]|吗[。！!]?\s*$')
HELP=re.compile(r'怎么办|怎么(?:弄|搞|做)|有人知道|求助')
CONFIRMED=re.compile(r'老师(?:已经|已)?(?:确认|通知)|(?:已经|已|正式)确认|正式通知|确定(?:改|在|为)|确认了|通知[：:]')
NOTICE=re.compile(r'作业|考试|报名|截止|提交|调课|停课|课程|上课|教室|地点|集合|材料|学习|测试|二田|体能')
TASK=re.compile(r'提交|交作业|交材料|补交|报名|完成.{0,20}(?:学习|考试|测试|作业)|进行.{0,16}(?:自学|考试|测试)|请.{0,16}(?:准备|带|填写|学习|参加|交)|带好|携带|领取|缴费|签字|签到')
ARRANGEMENT=re.compile(r'调课|停课|改到|改为|改成|改教室|换到|地点变更|教室变更|取消|延期|上课地点|考试|集合|班会|开学|放假|召开.{0,6}会议')
INFO=re.compile(r'成绩.{0,12}查询|查询.{0,12}成绩|查询方式|查询指引|制度|须知|操作指引|使用说明|规定')
NETWORK=re.compile(r'校园网|校内网')
NEW_COMMITMENT=re.compile(r'提交|报名|截止|补交|完成.{0,15}(?:学习|考试|测试)|进行.{0,16}(?:自学|考试|测试)|请.{0,15}(?:学习|考试|测试|交)|改到|改为|调课|停课')
CONDITIONAL=re.compile(r'(?:无法|不能|密码错误|登录不进去|登录失败|查不到|尚未完成|尚未提交|未通过|已报名).{0,20}(?:同学|学生)|(?:若|如果|如有).{0,24}(?:失败|异常|不成功|错误)')
AWARD=re.compile(r'获奖|一等奖|二等奖|三等奖|优秀内务')
OPTIONAL=re.compile(r'有意向|有意参加|自愿|感兴趣的同学')
ROLES=re.compile(r'^(?:请|各位|各班|班级|面向|仅限)*(班长|宣传委员|学习委员|团支书|教师|老师|辅导员)')
MEDIA_PLACEHOLDER=re.compile(r'无法识别|不可识别|无法读取|内容未取得|尚未取得|无有效|无可读|读取失败|合成图片附件')


def confirmed(text):
    return any(CONFIRMED.search(part) and not QUESTION.search(part) for part in re.split(r'[。！？?\n]',text) if part.strip())


def waiting_text(text):
    prose=re.sub(r'https?://\S+','',text)
    return bool(QUESTION.search(prose) and not confirmed(prose))


def relay_header(text):
    if not text.lstrip().startswith('#接龙'): return None
    match=re.search(r'(?m)^\s*\d+[.、]\s+',text)
    return text[:match.start()].replace('#接龙','').strip() if match else None


def relay_progress(text):
    header=relay_header(text)
    return header is not None and not re.search(r'请|务必|必须|截止|提交|填报|完成',header)


def ordinary_reply(text):
    return bool(len(text)<45 and re.search(r'呢|吧|\[(?:微笑|捂脸|表情)\]$',text) and not TASK.search(text) and not ARRANGEMENT.search(text))


def usable_text(text):
    if not text or MEDIA_PLACEHOLDER.search(text): return False
    chars=re.findall(r'[\u4e00-\u9fffA-Za-z0-9]',text)
    return len(chars)>=12 and len(set(chars))>=5 or len(chars)>=5 and bool(TASK.search(text) or ARRANGEMENT.search(text))


def topic(text):
    if re.search(r'上课|课程|调课|二田|教室',text): return 'course'
    if re.search(r'考试|测试|成绩',text): return 'exam'
    if re.search(r'作业|提交|材料',text): return 'submission'
    if '报名' in text: return 'registration'
    return 'notice'


@dataclass(frozen=True)
class Attention:
    bucket:str
    attention_level:str
    information_complete:bool
    await_more_context:bool
    action_required:bool
    relevant:bool|None
    reason:str


def assess(item,messages,blocks=(),context=None,*,legacy=False):
    context=context or {}
    ids=set(item.get('evidence_message_ids') or [])
    selected=[m for m in messages if not ids or m.get('message_id',m.get('id')) in ids]
    text='\n'.join(m.get('raw_text','') for m in selected)
    derived='\n'.join(b.get('text','') for b in blocks if b.get('source_type') in {'image_ocr','image_vision'} and any(m.get('block_id','').split('_MEDIA_')[0]==b.get('block_id','').split('_MEDIA_')[0] for m in selected))
    content=text+'\n'+derived
    relevant=item.get('relevant')
    def result(bucket,reason,*,complete=True,awaiting=False,action=False,relation=relevant):
        level='action' if bucket=='action' else 'fyi' if bucket=='fyi' else 'ignore'
        return Attention(bucket,level,complete,awaiting,action,relation,reason)
    image_only=selected and all(m.get('message_type') in {'image','file'} for m in selected)
    # A saved document's filename is valid media metadata, not fabricated OCR.
    named_file=any(m.get('message_type')=='file' and m.get('attachment_available') and m.get('attachment_name') for m in selected)
    if image_only and not usable_text(derived) and not named_file:
        return result('media_pending','附件尚无可核实的正文，等待解析或上下文。',complete=False,awaiting=True)
    if re.search(r'(?:详见|看|参照|按照|见|按).{0,6}(?:图片|上图|附件)',text) and not usable_text(derived) and not named_file:
        return result('unresolved','通知依赖尚未提供的图片或附件，保留文字等待完整依据。',complete=False,awaiting=True)
    if not content.strip() or not re.search(r'[\u4e00-\u9fffA-Za-z0-9]',content):
        return result('filtered','没有实际信息。',complete=False)
    from .filtering import ACK,CHATTER
    if selected and all(relay_progress(m.get('raw_text','')) or ordinary_reply(m.get('raw_text','')) or ACK.fullmatch(m.get('raw_text','').strip()) for m in selected):
        return result('filtered','接龙名单更新或普通回应，没有新的行动通知。')
    if ACK.fullmatch(text.strip()) or CHATTER.search(text) and not TASK.search(text) and not ARRANGEMENT.search(text):
        return result('filtered','普通聊天或回应，没有新的事项。')
    if item.get('analysis_origin')=='local_pending':
        return result('unresolved','原文暂存，尚未通过注意事项校验。',complete=False,awaiting=True)
    has_confirmation=confirmed(content)
    if any(waiting_text(m.get('raw_text','')) for m in selected) and not has_confirmation:
        if HELP.search(text) and not ARRANGEMENT.search(text):
            return result('filtered','普通求助或问答，不是对用户的行动通知。',complete=False)
        return result('unresolved','疑问、传言或未确认的安排，等待同群后续确认。',complete=False,awaiting=True)
    aliases=[a for a in context.get('wechat_self_aliases',[]) if a]
    named=any(a in content for a in aliases)
    if AWARD.search(text) and not NEW_COMMITMENT.search(text) and not named:
        return result('filtered','获奖等级或名单没有明确关联用户，也没有需要执行的事项。')
    activity=item.get('category') in {'event','registration','competition','other'} or re.search(r'报名|竞赛|活动|招新|讲座',item.get('title','')+' '+(item.get('action') or ''))
    if (item.get('participation')=='optional' or OPTIONAL.search(text)) and activity:
        return result('filtered','普通自愿活动，用户没有明确关注或参与。')
    if relevant is False:
        return result('filtered','已知与用户不相关。')
    if CONDITIONAL.search(text) and not named:
        return result('fyi','只针对特定异常情形；尚不知道用户是否符合，不要求人工确认。',relation=None)
    if NETWORK.search(content) and not NEW_COMMITMENT.search(text):
        return result('fyi','网络使用条件说明，不是新任务或新的时间安排。',action=False)
    if INFO.search(item.get('title','')+'\n'+text) and not NEW_COMMITMENT.search(text):
        return result('fyi','查询方式、制度或操作说明，自动归档供查阅。',action=False)
    declared=item.get('attention_level')
    if not legacy and declared=='ignore':
        return result('filtered','模型判断无需关注，未进入待确认。')
    if not legacy and declared=='fyi':
        return result('fyi',item.get('reason') or '无需确认的信息，自动归档。')
    if (not legacy and (not item.get('information_complete') or item.get('await_more_context'))) or len(re.sub(r'\W','',content))<4:
        return result('unresolved','主体或事实不完整，等待上下文。',complete=False,awaiting=True)
    if relevant is not True:
        return result('unresolved','与用户的直接相关性尚未确认。',complete=False,awaiting=True)
    role=ROLES.search(text.strip())
    if role and not re.search(r'全体同学|各位同学|所有同学',text):
        key='教师' if role[1]=='老师' else role[1]
        assertion=context.get('role_assertions',{}).get(key,{})
        if assertion.get('confirmed') and assertion.get('value')=='false':
            return result('filtered','已确认用户不具备通知要求的角色。',relation=False)
        if not assertion.get('confirmed') or assertion.get('value')!='true':
            return result('unresolved','通知要求的用户角色尚未确认。',complete=False,awaiting=True)
    schedule=bool(ARRANGEMENT.search(content) and (has_confirmation or not QUESTION.search(content)))
    explicit=any(a.get('kind')=='explicit' and a.get('evidence_ids') for a in item.get('actions',[]))
    task=bool((TASK.search(content) or explicit) and item.get('action_required') is True and item.get('participation')=='required')
    if (legacy or declared=='action') and (task or schedule):
        return result('action','与用户相关，事实明确，需要执行或会影响安排。',action=task or bool(item.get('action_required')))
    if item.get('category') in {'task','deadline','course_change','exam'} and not schedule and not task:
        return result('unresolved','行动对象、事实或执行要求不够明确。',complete=False,awaiting=True)
    return result('fyi','有参考价值，但没有明确的新行动或安排变化。',action=False)


def apply(item,decision):
    return {**item,'evidence_message_ids':list(dict.fromkeys(item.get('evidence_message_ids') or [])),
            'attention_level':decision.attention_level,'information_complete':decision.information_complete,
            'await_more_context':decision.await_more_context,'action_required':decision.action_required,
            'relevant':decision.relevant,'needs_review':decision.bucket=='action',
            'important':decision.bucket=='action'}


def personalize(item,context):
    """Keep a student's task separate from a class representative's collection."""
    assertions=(context or {}).get('role_assertions',{})
    def other_actor(text):
        match=ROLES.search(text or '')
        if not match: return False
        role='教师' if match[1]=='老师' else match[1]
        value=assertions.get(role,{})
        return not (value.get('confirmed') and value.get('value')=='true')
    actions=[a for a in item.get('actions',[]) if not other_actor(a.get('title',''))]
    excluded=[d for d in item.get('deadlines',[]) if other_actor(d.get('role',''))]
    if len(actions)==len(item.get('actions',[])) and not excluded: return item
    result={**item,'actions':actions,'deadlines':[d for d in item.get('deadlines',[]) if d not in excluded]}
    if any(d.get('time')==item.get('deadline') for d in excluded): result['deadline']=None
    known={d['id'] for d in result['deadlines']}
    result['actions']=[{**a,'deadline_ids':[ref for ref in a.get('deadline_ids',[]) if ref in known]} for a in actions]
    if actions:
        result['action']='；'.join(a['title'] for a in actions)
        result['summary']=result['action']+'。'
    result['uncertainties']=list(dict.fromkeys([*item.get('uncertainties',[]),'其他角色的汇总任务和期限保留在原文，未作为用户个人要求。']))[:20]
    return result


def persist(c,candidate_id,item,decision,*,original=None,previous_status='needs_review',fingerprint=''):
    stamp=now()
    old=c.execute('SELECT status FROM wechat_candidate WHERE id=?',(candidate_id,)).fetchone()
    status=old['status'] if old and old['status'] in {'accepted','ignored','context_resolved'} else 'needs_review' if decision.bucket=='action' else decision.bucket
    data=apply(item,decision)
    c.execute('UPDATE wechat_candidate SET status=?,data_json=?,important=?,relevant=?,attention_level=?,information_complete=?,await_more_context=?,attention_reason=?,auto_archived_at=? WHERE id=?',
              (status,encode(data),int(data['important']),None if data['relevant'] is None else int(data['relevant']),decision.attention_level,int(decision.information_complete),int(decision.await_more_context),decision.reason,stamp if decision.bucket!='action' else None,candidate_id))
    c.execute('INSERT INTO wechat_attention_state VALUES (?,?,?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET policy_version=excluded.policy_version,input_hash=excluded.input_hash,decision_json=excluded.decision_json,classified_at=excluded.classified_at',
              (candidate_id,VERSION,fingerprint,encode(original or item),previous_status,encode(asdict(decision)),stamp))
    return data


def register_media(c,batch_id,snapshot_id,message_id):
    c.execute("INSERT OR IGNORE INTO wechat_attention_media(message_id,batch_id,snapshot_id,status,reason,created_at) VALUES (?,?,?,'media_pending',?,?)",
              (message_id,batch_id,snapshot_id,'附件正文尚未可靠解析，保留原件。',now()))


def media_text(c,snapshot_id,message_id):
    return '\n'.join(row[0] for row in c.execute("SELECT b.text FROM raw_source_block b JOIN wechat_snapshot_message sm ON sm.snapshot_id=b.raw_snapshot_id AND sm.block_id=b.block_id WHERE sm.snapshot_id=? AND sm.message_id=? AND b.source_type IN ('image_ocr','image_vision')",(snapshot_id,message_id)))


def refresh_media(c,batch_id,snapshot):
    for mid in {m['message_id'] for m in snapshot['wechat']['messages'] if m['message_type'] in {'image','file'}}:
        c.execute('UPDATE wechat_attention_media SET snapshot_id=? WHERE message_id=?',(snapshot['id'],mid))
        if usable_text(media_text(c,snapshot['id'],mid)):
            c.execute("UPDATE wechat_attention_media SET status='resolved',resolved_at=? WHERE message_id=?",(now(),mid))


def reclassify_saved(personal,context):
    """Idempotent local upgrade of stored candidates. Never dispatch a model."""
    from ..source_metadata import snapshot_with_metadata
    decisions=[]
    with personal.connection(write=True) as c:
        rows=c.execute("SELECT ca.*,b.snapshot_id,b.chat_id FROM wechat_candidate ca JOIN wechat_batch b ON b.id=ca.batch_id WHERE ca.status NOT IN ('accepted','ignored','superseded','context_resolved') AND b.superseded_by IS NULL").fetchall()
        for row in rows:
            state=c.execute('SELECT * FROM wechat_attention_state WHERE candidate_id=?',(row['id'],)).fetchone()
            original=json.loads(state['original_data_json'] if state else row['data_json'])
            if (state is None and row['model_item_id']=='_pending' and row['status']=='unresolved'
                    and original.get('analysis_origin')=='local_pending'):
                # A standalone placeholder with no queued analysis or extraction
                # is not an analysed candidate.  Preserve its review state until
                # the ordinary intake/analysis flow actually processes it.
                job=c.execute('SELECT 1 FROM wechat_analysis_job WHERE batch_id=? LIMIT 1',(row['batch_id'],)).fetchone()
                other=c.execute("SELECT 1 FROM wechat_candidate WHERE batch_id=? AND model_item_id!='_pending' LIMIT 1",(row['batch_id'],)).fetchone()
                review=c.execute('SELECT extraction_id FROM raw_review WHERE raw_snapshot_id=?',(row['snapshot_id'],)).fetchone()
                if not job and not other and review and review['extraction_id'] is None:
                    continue
            signature=hashlib.sha256(encode([VERSION,original,context]).encode()).hexdigest()
            if state and state['policy_version']==VERSION and state['input_hash']==signature: continue
            snapshot=snapshot_with_metadata(c,c.execute('SELECT * FROM raw_snapshot WHERE id=?',(row['snapshot_id'],)).fetchone())
            source=snapshot['wechat']['messages']
            if original.get('analysis_origin')=='local_pending':
                primary={r[0] for r in c.execute("SELECT message_id FROM wechat_batch_message WHERE batch_id=? AND role='primary'",(row['batch_id'],))}
                source=[m for m in source if m['message_id'] in primary]
            decision=assess(original,source,snapshot['blocks'],context,legacy='attention_level' not in original)
            persist(c,row['id'],personalize(original,context),decision,original=original,previous_status=row['status'],fingerprint=signature)
            if decision.bucket=='media_pending':
                for mid in set(original.get('evidence_message_ids') or []):
                    register_media(c,row['batch_id'],row['snapshot_id'],mid)
            if decision.bucket=='unresolved':
                for message in snapshot['wechat']['messages']:
                    if message['message_id'] in set(original.get('evidence_message_ids') or []) and waiting_text(message['raw_text']):
                        remember(c,row['chat_id'],{**message,'id':message['message_id']},datetime.now(timezone.utc))
            c.execute('UPDATE raw_review SET version=version+1,updated_at=? WHERE raw_snapshot_id=?',(now(),row['snapshot_id']))
            decisions.append({'id':row['id'],'title':original['title'],**asdict(decision)})
        # Keep generic raw-review screens consistent with the attention inbox.
        for batch in {r['batch_id'] for r in rows}:
            active={r[0] for r in c.execute("SELECT model_item_id FROM wechat_candidate WHERE batch_id=? AND status='needs_review' AND attention_level='action'",(batch,))}
            row=c.execute('SELECT a.id,a.parsed_output FROM ai_extraction a JOIN raw_review r ON r.extraction_id=a.id JOIN wechat_batch b ON b.snapshot_id=r.raw_snapshot_id WHERE b.id=?',(batch,)).fetchone()
            if row and row['parsed_output']:
                projection=json.loads(row['parsed_output'])
                from .analysis import project_output
                items=[json.loads(r[0]) for r in c.execute("SELECT data_json FROM wechat_candidate WHERE batch_id=? AND status='needs_review' AND attention_level='action'",(batch,))]
                projection['events']=project_output({'items':items,'evidence':[]})['events']
                c.execute('UPDATE ai_extraction SET parsed_output=? WHERE id=?',(encode(projection),row['id']))
            classified=c.execute("SELECT 1 FROM wechat_candidate WHERE batch_id=? AND model_item_id!='_pending' LIMIT 1",(batch,)).fetchone()
            if active or classified:
                c.execute("UPDATE wechat_batch SET status=? WHERE id=? AND status NOT IN ('superseded','ignored')",('needs_review' if active else 'archived',batch))
                c.execute("UPDATE raw_review SET status=? WHERE raw_snapshot_id=(SELECT snapshot_id FROM wechat_batch WHERE id=?) AND status IN ('pending','archived')",('pending' if active else 'archived',batch))
    return decisions


def remember(c,chat_id,message,at):
    created=message.get('source_time') or at.isoformat()
    try: expiry=datetime.fromisoformat(created)
    except ValueError: expiry=at
    if expiry.tzinfo is None:
        from .scheduler import SHANGHAI
        expiry=expiry.replace(tzinfo=SHANGHAI)
    c.execute('INSERT OR IGNORE INTO wechat_context_buffer(message_id,chat_id,topic,created_at,expires_at) VALUES (?,?,?,?,?)',
              (message['id'],chat_id,topic(message['raw_text']),created,(expiry+timedelta(hours=24)).astimezone(timezone.utc).isoformat()))


def repeated_notice(c,chat_id,message):
    if message['message_type'] not in {'text','link'}: return False
    old=c.execute('SELECT source_time,first_scan_id FROM wechat_message WHERE chat_id=? AND raw_text=? AND ingest_seq<? ORDER BY ingest_seq DESC LIMIT 1',(chat_id,message['raw_text'],message['ingest_seq'])).fetchone()
    if not old: return False
    if old['source_time'] and message.get('source_time'): return old['source_time'][:10]==message['source_time'][:10]
    return old['first_scan_id']==message['first_scan_id']


def prune_processing(personal,context,at):
    """Old queued chat batches obey the same cheap triage as newly captured text."""
    from .filtering import classify
    count=0
    with personal.connection(write=True) as c:
        jobs=c.execute("SELECT j.id,j.batch_id,b.snapshot_id,b.chat_id,ch.config_json FROM wechat_analysis_job j JOIN wechat_batch b ON b.id=j.batch_id JOIN wechat_chat ch ON ch.id=b.chat_id WHERE j.status IN ('pending','retry','budget_deferred','error') AND EXISTS(SELECT 1 FROM wechat_candidate ca WHERE ca.batch_id=b.id AND ca.model_item_id='_pending' AND ca.status NOT IN ('accepted','ignored','superseded'))").fetchall()
        for job in jobs:
            source=[dict(m) for m in c.execute("SELECT m.* FROM wechat_batch_message bm JOIN wechat_message m ON m.id=bm.message_id WHERE bm.batch_id=? AND bm.role='primary'",(job['batch_id'],))]
            config=json.loads(job['config_json'])
            decisions=[classify(m,priority=config.get('priority','high'),aliases=context.get('wechat_self_aliases',[]),trusted_senders=config.get('trusted_senders',[])) for m in source]
            if not decisions or any(d.level not in {'fast_reject','await_context'} for d in decisions): continue
            awaiting=any(d.level=='await_context' for d in decisions)
            for message,decision in zip(source,decisions):
                if decision.level=='await_context': remember(c,job['chat_id'],message,at)
            c.execute('UPDATE wechat_analysis_job SET status=?,error_code=NULL WHERE id=?',('await_context' if awaiting else 'filtered',job['id']))
            c.execute('UPDATE wechat_batch SET status=? WHERE id=?',('await_context' if awaiting else 'archived',job['batch_id']))
            c.execute("UPDATE raw_review SET status='archived' WHERE raw_snapshot_id=? AND status='pending'",(job['snapshot_id'],))
            c.execute("UPDATE wechat_candidate SET status=?,attention_level='ignore',information_complete=0,await_more_context=?,attention_reason=? WHERE batch_id=? AND model_item_id='_pending'",('unresolved' if awaiting else 'filtered',int(awaiting),'名单更新和普通回应已在本地过滤；未确认事实等待上下文。',job['batch_id']))
            count+=1
    return count


def context_for(c,chat_id,primary,at):
    stamp=at.astimezone(timezone.utc).isoformat()
    c.execute("UPDATE wechat_context_buffer SET status='expired' WHERE status='waiting' AND expires_at<=?",(stamp,))
    topics={topic(m['raw_text']) for m in primary if confirmed(m['raw_text'])}
    if not topics: return []
    rows=c.execute("SELECT m.*,u.topic FROM wechat_context_buffer u JOIN wechat_message m ON m.id=u.message_id WHERE u.chat_id=? AND u.status='waiting' ORDER BY m.ingest_seq DESC LIMIT 8",(chat_id,)).fetchall()
    return [dict(row) for row in reversed(rows) if row['topic'] in topics]


def resolve_context(c,batch_id,item):
    primary=[dict(m) for m in c.execute("SELECT m.* FROM wechat_batch_message bm JOIN wechat_message m ON m.id=bm.message_id WHERE bm.batch_id=? AND bm.role='primary'",(batch_id,))]
    topics={topic(m['raw_text']) for m in primary if confirmed(m['raw_text'])}
    if not topics: return
    held=c.execute("SELECT u.* FROM wechat_context_buffer u JOIN wechat_batch_message bm ON bm.message_id=u.message_id WHERE bm.batch_id=? AND u.status='waiting'",(batch_id,)).fetchall()
    for row in held:
        if row['topic'] not in topics: continue
        c.execute("UPDATE wechat_context_buffer SET status='resolved',resolved_by_batch_id=? WHERE message_id=?",(batch_id,row['message_id']))
        old=c.execute("SELECT ca.id,ca.batch_id FROM wechat_candidate ca JOIN wechat_batch b ON b.id=ca.batch_id WHERE b.chat_id=? AND ca.status='unresolved' AND EXISTS(SELECT 1 FROM json_each(ca.data_json,'$.evidence_message_ids') WHERE value=?)",(row['chat_id'],row['message_id'])).fetchall()
        for candidate in old:
            if candidate['batch_id']!=batch_id:
                c.execute("UPDATE wechat_candidate SET status='context_resolved' WHERE id=?",(candidate['id'],))
                c.execute('UPDATE wechat_candidate SET previous_candidate_id=? WHERE id=?',(candidate['id'],batch_id+':'+item['id']))
