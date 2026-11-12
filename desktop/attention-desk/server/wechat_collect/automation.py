"""Opt-in future-only personal tasks; model relevance alone is never consent."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

from ..personal_store import PersonalError, encode
from ..user_context import UserContextStore
from ..cloud_sync.wechat_file_lease import immutable_evidence
from .deepseek_content_gate import binding_digest

POLICY = 'wechat-personal-actions-v2'
FILENAME = 'notification-automation.json'
SHA = re.compile(r'^[a-f0-9]{64}$')
ROLES = re.compile(r'副班长|班长|团支书|宣传委员|学习委员|体育委员|生活委员|文娱委员|组织委员|心理委员|纪律委员|教师|老师|辅导员|负责人|干部|委员')
CONDITIONAL = re.compile(r'如果|若|如有|有意|自愿|感兴趣|有兴趣|愿意|意向|有需要|有需求|报名成功|已报名|已参加|已完成|已经完成|报名者|参赛者|获选|当选|补考|重修|部分同学|以下同学|有关同学|相关同学|名单|获奖|入选|申请|未完成|未提交|未通过|尚未|需要的|想要|想参加|详见附件|详见图片|按附件|按照附件|附件内|附件里|图片中|图中')
UNIVERSAL = re.compile(r'全体同学|所有同学|全班同学|全班|每位同学|各位同学|同学们|@(?:所有人|全体成员)(?=[\s，,。！!：:]|$)')
ACTION_WORDS=re.compile(r'提交|完成|填写|携带|带好|领取|缴费|签字|签到|交|到场|参加|报名|体测|测试|考试|集合|上课|学习|打印|下载|回复|查收')
NEGATED_ACTION=re.compile(r'(?:无需|不用|不必|无须|免于|不再|取消)[^。！？!?；;\n]{0,20}(?:提交|完成|填写|携带|领取|缴费|签字|签到|到场|参加|报名|体测|测试|考试|集合|上课|学习|打印|下载|回复)|(?:提交|缴费|考试|集合|上课|活动)[^。！？!?；;\n]{0,8}(?:取消|暂停)')


def instant(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ValueError('automation_timestamp_required')
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class Activation:
    value: dict
    digest: str
    directory: Path

    def check(self, *, permission, at=None):
        v = self.value
        current = json.loads((self.directory / FILENAME).read_bytes())
        if hashlib.sha256(encode(current).encode()).hexdigest() != self.digest:
            raise ValueError('automation_approval_changed')
        release=json.loads((self.directory/'cloud-sync/notification-automation/release.json').read_bytes())
        from ..cloud_sync.notification_runtime import implementation_hash
        if (release['proof']['api_scope']['activation_hash']!=self.digest
                or release['implementation_hash']!=implementation_hash()):
            raise ValueError('automation_release_changed')
        if v['permissions'].get(permission) is not True:
            raise ValueError('automation_permission_missing')
        controls = json.loads((self.directory / 'recovery-controls.json').read_bytes())
        if (controls.get('version')!=1 or
                any(type(controls.get(k)) is not bool for k in ('pause_paid_ai','hold_cloud_uploads'))):
            raise ValueError('automation_global_controls_invalid')
        key='hold_cloud_uploads' if permission=='publish_tasks' else 'pause_paid_ai'
        if controls[key]:raise ValueError('global_automation_paused')
        if UserContextStore(self.directory).read().fingerprint() != v['profile_hash']:
            raise ValueError('automation_profile_changed')
        if at is not None and instant(at) < instant(v['not_before']):
            raise ValueError('automation_not_started')
        return self

    def contains(self, c, batch):
        source = self.value['sources'].get(batch['chat_id'])
        chat = c.execute('SELECT * FROM wechat_chat WHERE id=?', (batch['chat_id'],)).fetchone()
        if (not source or not chat or not chat['enabled']
                or binding_digest(chat['source_id'], chat['binding_json']) != source['binding_hash']):
            return False
        rows = c.execute("SELECT m.* FROM wechat_message m JOIN wechat_batch_message bm ON bm.message_id=m.id WHERE bm.batch_id=? AND bm.role='primary'", (batch['id'],)).fetchall()
        lower = instant(self.value['not_before'])
        return bool(rows) and all(r['ingest_seq'] > source['after_seq']
            and r['time_reliability'] == 'reliable'
            and instant(r['source_time']) >= lower and instant(r['captured_at']) >= lower for r in rows)


@contextmanager
def activation(directory, *, permission='analyze', at=None):
    directory = Path(directory).resolve()
    with immutable_evidence(directory / FILENAME, max_bytes=16384) as raw:
        v = json.loads(raw)
        if (set(v) != {'version','policy','enabled','authorization_record','approved_at','not_before',
                       'sources','profile_hash','permissions','http_daily','tokens_daily'}
                or v['version'] != 2 or v['policy'] != POLICY or v['enabled'] is not True
                or not isinstance(v['authorization_record'], str) or not v['authorization_record'].strip()
                or not SHA.fullmatch(v['profile_hash']) or not isinstance(v['sources'], dict)
                or not 1 <= len(v['sources']) <= 5
                or set(v['permissions']) != {'analyze','create_tasks','publish_tasks'}
                or any(type(b) is not bool for b in v['permissions'].values())
                or type(v['http_daily']) is not int or not 30 <= v['http_daily'] <= 10000
                or v['tokens_daily'] is not None and (type(v['tokens_daily']) is not int or v['tokens_daily'] < 100000)
                or instant(v['approved_at']) > instant(v['not_before'])):
            raise ValueError('invalid_automation_approval')
        for source in v['sources'].values():
            if (set(source) != {'after_seq','binding_hash','audience'} or type(source['after_seq']) is not int
                    or source['after_seq'] < 0 or not SHA.fullmatch(source['binding_hash'])):
                raise ValueError('invalid_automation_source')
            if (set(source['audience']) != {'kind','value'} or source['audience']['kind'] not in {'class','year','course','review'}
                    or not isinstance(source['audience']['value'],str)):
                raise ValueError('invalid_automation_audience')
        yield Activation(v, hashlib.sha256(encode(v).encode()).hexdigest(), directory).check(permission=permission, at=at)


def eligibility(item, evidence, context, primary, audience=None, source_rows=None):
    """An explicit source-backed obligation under independently confirmed scope."""
    if (item.get('is_school_notice') is not True or item.get('relevant') is not True or item.get('action_required') is not True
            or item.get('attention_level') != 'action' or item.get('information_complete') is not True
            or item.get('await_more_context') or item.get('needs_review') or item.get('uncertainties')
            or item.get('participation') != 'required' or item.get('notice_kind') != 'new'
            or item.get('confidence', 0) < .90 or not item.get('actions')
            or not set(item.get('evidence_message_ids', [])) <= primary):
        return False
    if not source_rows:return False
    times=[d['time'] for d in item['deadlines']]+[item[k] for k in ('deadline','start_time','end_time') if item[k]]
    if any(t['uncertain'] or t['precision']=='unknown' or not t['value'] for t in times):return False
    scope=audience or {'kind':'review','value':''}
    quotes = {q['id']: q for q in evidence}
    audience_mappings = [m for m in item['evidence_mapping'] if m['field'] == 'target_people']
    audience_text = '\n'.join(quotes[r]['quote'] for m in audience_mappings for r in m['evidence_ids'] if r in quotes)
    for action in item['actions']:
        if action['kind'] != 'explicit' or not action['evidence_ids']:
            return False
        text = '\n'.join(quotes[r]['quote'] for r in action['evidence_ids'] if r in quotes)
        if not text or CONDITIONAL.search(text) or re.search(r'[?？]|听说|好像|可能|是不是', text):
            return False
        clauses=[]
        full=[]
        for ref in action['evidence_ids']:
            q=quotes.get(ref);row=source_rows.get(q['message_id']) if q else None
            if not row or row['raw_text'][q['start']:q['end']]!=q['quote']:return False
            original=row['raw_text'];full.append(original)
            # Use the entire source sentence, not a model-selected subspan.
            boundaries=[m.end() for m in re.finditer(r'[。！？!?；;\n]',original[:q['start']])]
            start=boundaries[-1] if boundaries else 0
            end=re.search(r'[。！？!?；;\n]',original[q['end']:])
            clauses.append(original[start:q['end']+end.end() if end else len(original)])
            prefix=original[:start].strip().splitlines()
            if prefix and prefix[-1].endswith(('：',':')):clauses.append(prefix[-1])
        if any(CONDITIONAL.search(s) or NEGATED_ACTION.search(s) for s in full):return False
        global_scopes=[m.group(1) for s in full for m in re.finditer(r'(?:仅限|只限|面向|只有|通知对象[：:]?)\s*([^。；;\n]{1,80})',s)]
        for required in global_scopes:
            roles=set(ROLES.findall(required))
            if roles and any(context.role('教师' if r=='老师' else r)!='true' for r in roles):return False
            head=re.split(r'[，,：:]|请|务必|必须|提交|完成|填写|携带|带好|领取|缴费|签字|签到|到场|参加|报名|体测|测试|考试|集合|上课|打印|下载|回复|查收',required,1)[0].strip()
            if not head:return False
            remaining=head
            remaining=UNIVERSAL.sub('',remaining)
            for role in context.role_assertions:
                if context.role(role)=='true':remaining=remaining.replace(role,'')
            if 'class_name' in context.confirmed_fields and context.class_name:remaining=remaining.replace(context.class_name,'')
            if 'courses' in context.confirmed_fields:
                for course in context.courses:remaining=remaining.replace(course,'')
                if scope['kind']=='course' and scope['value'] in context.courses:
                    remaining=remaining.replace('本课程','').replace('该课','')
            if 'entry_year' in context.confirmed_fields:remaining=remaining.replace(str(context.entry_year)+'级','')
            remaining=re.sub(r'全体|所有|各位|全班|每位|本班|同学|学生|以及|和|及|的|[\s、]','',remaining)
            if remaining:return False  # Unconfirmed sex, membership or other qualification is not universal.
        applicable = audience_text+'\n'+'\n'.join([*clauses,*global_scopes])
        # An action quote that still mixes a class officer's task is not safe.
        roles = set(ROLES.findall(applicable))
        if roles and any(context.role('教师' if r == '老师' else r) != 'true' for r in roles):
            return False
        if not roles:
            class_name = context.class_name if 'class_name' in context.confirmed_fields else ''
            direct = any(re.search(r'(?<![A-Za-z0-9_@])@'+re.escape(a)+r'(?=$|[\s，,。！!：:；;])',applicable) for a in context.wechat_self_aliases if a)
            group_member = bool(
                scope['kind']=='class' and 'class_name' in context.confirmed_fields and context.class_name==scope['value']
                or scope['kind']=='year' and 'entry_year' in context.confirmed_fields and str(context.entry_year)==scope['value']
                or scope['kind']=='course' and 'courses' in context.confirmed_fields and scope['value'] in context.courses)
            if not (class_name and class_name in applicable or direct or UNIVERSAL.search(applicable) and group_member):
                return False
        # Different year/class/course qualifications must be confirmed too.
        for year in re.findall(r'(20\d{2})级', applicable):
            if 'entry_year' not in context.confirmed_fields or context.entry_year != int(year):
                return False
        classes = re.findall(r'(?:\d{2}|20\d{2})[\u4e00-\u9fff]{1,12}\d+班', applicable)
        if classes and (not context.class_name or any(s not in context.class_name for s in classes)):
            return False
        if re.search(r'选修|修读|该课|本课程|参加.{0,8}课程', applicable):
            bound_course=(scope['kind']=='course' and 'courses' in context.confirmed_fields and scope['value'] in context.courses)
            if not bound_course and not any(course and course in applicable for course in context.courses):
                return False
        if not ACTION_WORDS.search(text):
            return False
    return True


def confirmation(item, evidence, review):
    from ..extraction_models import ConfirmationInput
    by_id = {q['id']: {'block_id':q['block_id'],'quote':q['quote'],
                      'start_offset':q['start'],'end_offset':q['end']} for q in evidence}
    def refs(ids): return [by_id[r] for r in ids]
    from .analysis import project_output
    projected = project_output({'items':[item], 'evidence':evidence})['events'][0]
    deadlines = []
    for d in projected['deadlines']:
        if d['uncertain'] or d['precision'] == 'unknown' or d['relation'] != 'current':
            raise ValueError('automatic_deadline_uncertain')
        deadlines.append({'id':d['id'],'role':d['role'],'value':d['normalized_date'],
            'precision':d['precision'],'end_value':d['end_value'],'range_precision':d['range_precision'],
            'original_text':d['original_text'],'evidence':refs(d['evidence_ids']), 'confirmed_by_user':False})
    mappings = {m['field']: m['evidence_ids'] for m in item['evidence_mapping']}
    actions=[];seen=set()
    for a in item['actions']:
        for ref in a['evidence_ids']:
            quote=by_id[ref];title=quote['quote'].strip()
            if not ACTION_WORDS.search(title):continue
            if not title or len(title)>300:raise ValueError('automatic_action_requires_short_literal_evidence')
            if title in seen:continue
            seen.add(title)
            action_id='auto_'+hashlib.sha256(encode(quote).encode()).hexdigest()[:24]
            actions.append({'id':action_id,'title':title,'kind':'explicit',
                            'deadline_id':a['deadline_ids'][0] if a['deadline_ids'] else None,'evidence':refs(a['evidence_ids'])})
    if not actions:raise ValueError('automatic_literal_action_missing')
    event = {'id':item['id'],'title':actions[0]['title'][:200],'summary':'\n'.join(a['title'] for a in actions),'category':item['category'],
        'relevance':'yes','participation':'yes','notice_kind':'new','deadlines':deadlines,
        'audience':{'text':'、'.join(item['target_people']), 'evidence':refs(mappings.get('target_people', []))} if item['target_people'] else None,
        'actions':actions,
        'locations':[{'text':item['location'],'evidence':refs(mappings.get('location', []))}] if item['location'] else []}
    return ConfirmationInput.model_validate({'expected_version':review['version'],'extraction_id':review['extraction_id'],
        'events':[event], 'acknowledged_warnings':False}, context={'automatic_policy':POLICY})


@dataclass(frozen=True)
class AutomaticDecision:
    grant: Activation
    batch_id: str
    items: tuple
    evidence: list
    payload_hash: str
    job_id: str
    attempt_id: str
    source_keys: dict
    duplicate_items: tuple=()

    def event_id(self, item_id):
        return str(uuid5(NAMESPACE_URL, POLICY + ':' + self.source_keys[item_id]))

    def validate(self, c, payload):
        self.grant.check(permission='create_tasks')
        batch = c.execute('SELECT * FROM wechat_batch WHERE id=?', (self.batch_id,)).fetchone()
        if not batch or not self.grant.contains(c, batch) or hashlib.sha256(encode(payload.model_dump()).encode()).hexdigest() != self.payload_hash:
            raise PersonalError('自动事项的授权或来源已改变。', 'automation_changed', 409)
        primary = {r[0] for r in c.execute("SELECT message_id FROM wechat_batch_message WHERE batch_id=? AND role='primary'", (self.batch_id,))}
        source_rows={r['id']:dict(r) for r in c.execute("SELECT m.* FROM wechat_message m JOIN wechat_batch_message bm ON bm.message_id=m.id WHERE bm.batch_id=? AND bm.role='primary'",(self.batch_id,))}
        for item in self.items:
            saved=c.execute('SELECT data_json FROM wechat_candidate WHERE batch_id=? AND model_item_id=?',(self.batch_id,item['id'])).fetchone()
            if (not saved or json.loads(saved['data_json'])!=item
                    or not eligibility(item,self.evidence,UserContextStore(self.grant.directory).read(),primary,
                                       self.grant.value['sources'][batch['chat_id']]['audience'],source_rows)):
                raise PersonalError('个人适用性或行动依据已改变。', 'automation_changed', 409)

    def receipt(self,item_id):
        return {'policy':POLICY,'activation_hash':self.grant.digest,'source_key':self.source_keys[item_id],
                'job_id':self.job_id,'attempt_id':self.attempt_id,'batch_id':self.batch_id}

    def finish_duplicates(self,c):
        for item,owner_id in self.duplicate_items:
            c.execute("UPDATE wechat_candidate SET status='ignored',event_id=?,attention_reason='相同原文行动已自动生成，未重复创建。' WHERE batch_id=? AND model_item_id=? AND status='needs_review' AND data_json=?",
                      (self.event_id(owner_id),self.batch_id,item['id'],encode(item)))


def promote(personal, grant, *, job_ids):
    """Only newly authorized completed jobs; every task and receipt commit together."""
    from ..review_store import ReviewStore
    result = {'created':0,'pending':0,'duplicates':0}
    for job_id in job_ids:
        with personal.connection() as c:
            job = c.execute("SELECT * FROM wechat_analysis_job WHERE id=? AND status='parsed'", (job_id,)).fetchone()
            if not job: continue
            batch = c.execute('SELECT * FROM wechat_batch WHERE id=?', (job['batch_id'],)).fetchone()
            if not grant.contains(c,batch): continue
            if batch['snapshot_id']!=job['snapshot_id']:
                from .analysis import AnalysisQueue
                completed=AnalysisQueue.completed_job(c,batch)
                if not completed or completed['id']!=job_id:continue
            extraction = c.execute('SELECT * FROM ai_extraction WHERE id=?', (job['extraction_id'],)).fetchone()
            candidates = [json.loads(r[0]) for r in c.execute("SELECT data_json FROM wechat_candidate WHERE batch_id=? AND status='needs_review' AND model_item_id!='_pending'", (batch['id'],))]
            if not candidates:continue
            attempts = c.execute("SELECT id FROM wechat_llm_attempt WHERE job_id=? AND status='success' AND kind='text'", (job_id,)).fetchall()
            primary = {r[0] for r in c.execute("SELECT message_id FROM wechat_batch_message WHERE batch_id=? AND role='primary'", (batch['id'],))}
            source_rows={r['id']:dict(r) for r in c.execute("SELECT m.* FROM wechat_message m JOIN wechat_batch_message bm ON bm.message_id=m.id WHERE bm.batch_id=? AND bm.role='primary'",(batch['id'],))}
        # Rebuild from the saved provider receipt without another API request.
        from .analysis import primary_message_ids, snapshot_with_metadata
        from .deepseek_content_gate import leased_grant, prepare_authorized_projection
        with leased_grant(personal) as allowed:
            with personal.connection() as c:
                b = dict(batch); b['primary_ids'] = primary_message_ids(c,b['id'])
                snapshot = snapshot_with_metadata(c,c.execute('SELECT * FROM raw_snapshot WHERE id=?',(job['snapshot_id'],)).fetchone())
            projection = prepare_authorized_projection(personal,b,snapshot,allowed)
            with personal.connection() as c:
                raw = c.execute("SELECT raw_output FROM wechat_llm_attempt WHERE job_id=? AND status='success' AND kind='text'", (job_id,)).fetchall()
            if len(raw) != 1 or len(attempts) != 1: continue
            parsed,_ = projection.restore(raw[0][0],json.loads(job['profile_json']))
        evidence = parsed['evidence']
        expected = {i['id']:i for i in parsed['items']}
        approved=[]; events=[]; keys={}; duplicate_items=[]
        with personal.connection() as c:
            review=c.execute('SELECT * FROM raw_review WHERE raw_snapshot_id=?',(batch['snapshot_id'],)).fetchone()
        for item in candidates:
            context = UserContextStore(personal.path.parent).read()
            if (expected.get(item['id']) != item or not eligibility(item,evidence,context,primary,
                    grant.value['sources'][batch['chat_id']]['audience'],source_rows)):
                result['pending'] += 1; continue
            with personal.connection() as c:
                canonical=sorted({re.sub(r'\s+',' ',q['quote']).strip() for a in item['actions'] for q in evidence if q['id'] in a['evidence_ids']})
                from .scheduler import SHANGHAI
                days=sorted({instant(source_rows[mid]['source_time']).astimezone(SHANGHAI).date().isoformat() for mid in item['evidence_message_ids']})
                dates=[d['time']['value'] for d in item['deadlines']]
                dates.extend(item[k]['value'] for k in ('deadline','start_time','end_time') if item[k])
                originals=sorted({re.sub(r'\s+',' ',source_rows[mid]['raw_text']).strip() for mid in item['evidence_message_ids']})
                key=hashlib.sha256(encode([POLICY,batch['chat_id'],grant.value['sources'][batch['chat_id']]['binding_hash'],originals,canonical,sorted(set(dates)) if dates else days]).encode()).hexdigest()
                eid = str(uuid5(NAMESPACE_URL,POLICY+':'+key))
                duplicate=c.execute("SELECT 1 FROM user_decision WHERE event_id=? AND action='wechat_auto_created' AND json_extract(after_json,'$.automation.source_key')=?",(eid,key)).fetchone()
                existing=c.execute('SELECT 1 FROM event WHERE id=?',(eid,)).fetchone()
            if existing:
                if duplicate:
                    grant.check(permission='create_tasks')
                    with personal.connection(write=True) as write:
                        changed=write.execute("UPDATE wechat_candidate SET status='ignored',event_id=?,attention_reason='相同来源与期限的个人事项已存在，保留本条消息但不重复创建。' WHERE batch_id=? AND model_item_id=? AND status='needs_review' AND data_json=?",(eid,batch['id'],item['id'],encode(item)))
                        if changed.rowcount and not write.execute("SELECT 1 FROM wechat_candidate WHERE batch_id=? AND status='needs_review'",(batch['id'],)).fetchone():
                            write.execute("UPDATE raw_review SET status='archived',version=version+1 WHERE raw_snapshot_id=? AND status='pending'",(batch['snapshot_id'],))
                            write.execute("UPDATE wechat_batch SET status='archived' WHERE id=?",(batch['id'],))
                    result['duplicates']+=1
                continue
            try: proposed=confirmation(item,evidence,review)
            except ValueError:
                result['pending']+=1; continue
            if key in keys.values():
                duplicate_items.append((item,next(i for i,k in keys.items() if k==key)))
                continue
            approved.append(item); keys[item['id']]=key; events.extend(proposed.events)
        if approved:
            from ..extraction_models import ConfirmationInput
            payload=ConfirmationInput.model_validate({'expected_version':review['version'],'extraction_id':review['extraction_id'],
                'events':[e.model_dump() for e in events]},context={'automatic_policy':POLICY})
            decision = AutomaticDecision(grant,batch['id'],tuple(approved),evidence,
                hashlib.sha256(encode(payload.model_dump()).encode()).hexdigest(),job_id,attempts[0][0],keys,tuple(duplicate_items))
            ReviewStore(personal).confirm(batch['snapshot_id'],payload,automatic=decision)
            result['created'] += len(approved)
    return result
