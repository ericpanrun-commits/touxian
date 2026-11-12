"""Regressions from independent review of the new automatic closure."""
import asyncio
from datetime import timedelta
import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from server.ai_transport import DeepSeekTransport
from server.personal_store import encode
from server.tests_wechat.test_queue_starvation_regression import rig
from server.tests_wechat.test_notification_automation import enable,fresh,counts
from server.tests_wechat.test_pipeline import output_for
from server.cloud_sync.notification_runtime import progress_health
from server.notification_health import snapshot


@pytest.mark.parametrize('enqueued',[False,True])
@pytest.mark.parametrize('catchup',[False,True])
def test_post_activation_notice_resumes_after_25_hours_within_its_existing_future_grant(rig,enqueued,catchup):
    old=rig.add('请全体同学提交旧积压实验报告。');enable(rig)
    batch=fresh(rig)
    if enqueued:rig.worker.queue.enqueue(batch['id'],rig.service.context.read())
    if catchup:
        with rig.personal.connection(write=True) as c:
            scan=c.execute('SELECT scan_id FROM wechat_batch WHERE id=?',(batch['id'],)).fetchone()[0]
            c.execute('UPDATE wechat_scan SET stats_json=? WHERE id=?',(encode({'recovery_requires_approval':1}),scan))
    rig.clock[0]+=timedelta(hours=25)
    result=asyncio.run(rig.worker.cycle())
    assert result['calls']==result['created']==1
    with rig.personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_analysis_job WHERE batch_id=?',(old['id'],)).fetchone()[0]==0


def test_scope_excluded_running_marker_is_preserved_without_blocking_exclusive_new_worker(rig):
    old=rig.add('请全体同学提交旧积压实验报告。')
    old_job=rig.worker.queue.enqueue(old['id'],rig.service.context.read())
    with rig.personal.connection(write=True) as c:
        c.execute("UPDATE wechat_analysis_job SET status='running' WHERE id=?",(old_job,))
        c.execute("INSERT INTO wechat_llm_attempt(id,job_id,attempt_number,kind,started_at,status,reserved_tokens) VALUES (?,?,1,'text',?,'running',100)",(str(uuid4()),old_job,rig.clock[0].isoformat()))
        before=[tuple(r) for r in c.execute('SELECT * FROM wechat_llm_attempt')]
    enable(rig);fresh(rig)
    assert asyncio.run(rig.worker.cycle())['created']==1
    with rig.personal.connection() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM wechat_llm_attempt WHERE job_id=?',(old_job,))]==before
        assert c.execute('SELECT status FROM wechat_analysis_job WHERE id=?',(old_job,)).fetchone()[0]=='running'


@pytest.mark.parametrize('text,quote',[
    ('请名单内的全体同学提交实验报告。','全体同学提交实验报告'),
    ('仅限已报名的同学：\n请全体同学提交实验报告。','全体同学提交实验报告'),
    ('请团支书通知全体同学提交实验报告。','全体同学提交实验报告'),
])
def test_true_but_shortened_quote_cannot_drop_personal_eligibility_conditions(rig,text,quote):
    enable(rig);fresh(rig,text)
    def response(request):
        p=json.loads(json.loads(request.content)['messages'][-1]['content'].split('仅处理以下数据:\n')[1]);out=output_for(p)
        start=p['blocks'][-1]['text'].index(quote)
        out['evidence'][0].update(quote=quote,start=start,end=start+len(quote))
        out['items'][0]['target_people']=['全体同学']
        out['items'][0]['evidence_mapping'].append({'field':'target_people','evidence_ids':['q1']})
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(out,ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'total_tokens':100}})
    rig.worker.queue.transport=DeepSeekTransport(httpx.MockTransport(response))
    assert asyncio.run(rig.worker.cycle())['calls']==1
    assert counts(rig)['event']==0


def test_activation_cannot_be_edited_without_a_new_reviewed_release_binding(rig):
    value=enable(rig);fresh(rig);value['http_daily']=5000
    (rig.personal.path.parent/'notification-automation.json').write_text(encode(value),'utf-8')
    assert asyncio.run(rig.worker.cycle())['state']=='automation_approval_required'
    assert counts(rig)['wechat_llm_attempt']==0


def test_v2_evidence_hash_includes_activation_and_v1_remains_four_file_stable():
    from server.cloud_sync.deepseek_cloud_evidence import EVIDENCE_FILES,evidence_digest
    base={name:b'synthetic evidence' for name in EVIDENCE_FILES}
    expected=hashlib.sha256(json.dumps({name:hashlib.sha256(base[name]).hexdigest() for name in EVIDENCE_FILES},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert evidence_digest(base)==expected
    assert evidence_digest({**base,'notification-automation.json':b'A'})!=evidence_digest({**base,'notification-automation.json':b'B'})


def test_continuous_network_failure_alerts_even_with_fresh_heartbeats(rig):
    enable(rig);directory=rig.personal.path.parent;previous={}
    for _ in range(3):previous=progress_health({'state':'network_unavailable'},previous)
    assert previous['alert_required']
    at=rig.clock[0].isoformat()
    (directory/'deepseek-worker-state.json').write_text(encode({'last_tick':at,'state':'ready'}),'utf-8')
    (directory/'cloud-sync/notification-automation/status.json').write_text(encode({**previous,'at':at}),'utf-8')
    assert snapshot(directory,rig.clock[0])['alert_required']


def check_attachment_order(rig,create_first):
    from server.wechat_collect.file_evidence import enrich_existing
    from server.wechat_collect.ingestion import prepare_batch
    from server.wechat_collect.batching import BatchPlan
    from server.wechat_collect.deepseek_content_gate import leased_grant
    from server.cloud_sync.automatic_actions import build_automatic_preview
    from server.wechat_collect.models import ObservedMessage,Observation
    enable(rig);rig.clock[0]+=timedelta(minutes=15)
    scan,_=rig.service.repository.begin_scan('isolated-mixed-notice',1)
    chat=rig.service.repository.chat('test');stamp=rig.clock[0].isoformat()
    rig.service.repository.capture(scan['id'],chat,Observation('test','unverified',(
        ObservedMessage('合成说明.pdf',message_type='file',source_time=stamp,time_reliability='reliable'),
        ObservedMessage('请全体同学提交实验报告。',source_time=stamp,time_reliability='reliable')),True,True))
    rig.service.repository.finish_scan(scan['id'],'changed',{})
    with rig.personal.connection() as c:
        rows=c.execute('SELECT id,message_type FROM wechat_message ORDER BY ingest_seq').fetchall()
    mid=next(r['id'] for r in rows if r['message_type']=='file')
    batch=prepare_batch(rig.personal,chat,scan['id'],BatchPlan(tuple(r['id'] for r in rows),(),False),at=rig.clock[0])
    with rig.personal.connection(write=True) as c:
        c.execute('INSERT INTO wechat_media_work(message_id,reference_json) VALUES (?,?)',(mid,encode({'kind':'file','title':'合成说明.pdf'})))
    if create_first:
        assert asyncio.run(rig.worker.cycle())['created']==1
    else:
        from unittest.mock import patch
        with patch('server.wechat_collect.automation.promote',side_effect=OSError('crash after parsed')):
            with pytest.raises(OSError):asyncio.run(rig.worker.cycle())
    with rig.personal.connection() as c:
        job=c.execute('SELECT id FROM wechat_analysis_job WHERE batch_id=?',(batch,)).fetchone()[0]
        history=[tuple(r) for r in c.execute('SELECT * FROM event')]
    before=None
    if create_first:
        with leased_grant(rig.personal) as grant:
            with rig.personal.connection() as c:before=build_automatic_preview(c,grant,job)
    assert mid in enrich_existing(rig.personal)
    if not create_first:
        recovered=asyncio.run(rig.worker.cycle())
        assert recovered['created']==1 and recovered['calls']==0
        with rig.personal.connection() as c:history=[tuple(r) for r in c.execute('SELECT * FROM event')]
    with leased_grant(rig.personal) as grant:
        with rig.personal.connection() as c:value=build_automatic_preview(c,grant,job)
    assert any(r['kind']=='event' for r in value['bundle']['records'])
    if before is not None:assert before==value
    with rig.personal.connection() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM event')]==history
        assert c.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0]==1


def test_automatic_task_survives_real_attachment_refresh_without_reanalysis_or_history_change(rig):
    check_attachment_order(rig,True)


def test_parse_then_enrich_then_recover_creation_uses_the_committed_event_snapshot(rig):
    check_attachment_order(rig,False)


@pytest.mark.parametrize('name,expected',[('Eric',True),('EricPlus',False)])
def test_direct_alias_uses_exact_mention_boundaries(name,expected):
    from server.user_context import UserContext
    from server.wechat_collect.automation import eligibility
    text='@'+name+' 请提交实验报告。';message={'message_id':'m','block_id':'b'}
    out=output_for({'batch_id':'batch','messages':[message],'blocks':[{'block_id':'b','text':text}]})
    item=out['items'][0];context=UserContext(wechat_self_aliases=['Eric'])
    assert eligibility(item,out['evidence'],context,{'m'},{'kind':'review','value':''},{'m':{'raw_text':text}}) is expected


def test_global_role_header_cannot_be_lost_across_sentences(rig):
    enable(rig);fresh(rig,'本通知仅限体育委员。\n请全体同学提交实验报告。')
    def respond(request):
        p=json.loads(json.loads(request.content)['messages'][-1]['content'].split('仅处理以下数据:\n')[1]);out=output_for(p)
        quote='全体同学提交实验报告';start=p['blocks'][-1]['text'].index(quote)
        out['evidence'][0].update(quote=quote,start=start,end=start+len(quote))
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(out,ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'total_tokens':100}})
    rig.worker.queue.transport=DeepSeekTransport(httpx.MockTransport(respond))
    assert asyncio.run(rig.worker.cycle())['created']==0


def test_confirmed_course_binding_resolves_course_pronouns():
    from server.user_context import UserContext
    from server.wechat_collect.automation import eligibility
    text='本课程全体同学明天上课请携带体测证。'
    out=output_for({'batch_id':'batch','messages':[{'message_id':'m','block_id':'b'}],'blocks':[{'block_id':'b','text':text}]})
    context=UserContext(courses=['合成体能课'],confirmed_fields=['courses'])
    assert eligibility(out['items'][0],out['evidence'],context,{'m'},{'kind':'course','value':'合成体能课'},{'m':{'raw_text':text}})


@pytest.mark.parametrize('text',['@所有人 请提交实验报告。','请全班同学提交实验报告。'])
def test_generic_audience_notices_publish_minimized_evidence_without_losing_the_task(rig,text):
    from server.wechat_collect.deepseek_content_gate import leased_grant
    from server.cloud_sync.automatic_actions import build_automatic_preview
    enable(rig);fresh(rig,text)
    assert asyncio.run(rig.worker.cycle())['created']==1
    with leased_grant(rig.personal) as grant:
        with rig.personal.connection() as c:
            job=c.execute('SELECT id FROM wechat_analysis_job').fetchone()[0]
            value=build_automatic_preview(c,grant,job)
    wire=encode(value['bundle']['records'])
    assert '@所有人' not in wire
    assert any(r['kind']=='event' for r in value['bundle']['records'])


@pytest.mark.parametrize('restriction',['女生','团员','参加比赛的同学'])
def test_unconfirmed_global_restriction_is_not_erased_by_later_universal_words(rig,restriction):
    enable(rig);fresh(rig,'本通知仅限'+restriction+'，请全体同学提交实验报告。')
    assert asyncio.run(rig.worker.cycle())['created']==0


def test_same_day_identical_repost_keeps_one_event_without_modifying_it(rig):
    enable(rig);fresh(rig)
    assert asyncio.run(rig.worker.cycle())['created']==1
    with rig.personal.connection() as c:original=[tuple(r) for r in c.execute('SELECT * FROM event')]
    fresh(rig)
    result=asyncio.run(rig.worker.cycle())
    assert result['duplicates']==1 and result['created']==0
    assert counts(rig)=={'event':1,'task':1,'wechat_llm_attempt':2}
    with rig.personal.connection() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM event')]==original


def test_model_action_wording_cannot_replace_the_literal_instruction_or_duplicate_it(rig):
    import copy
    enable(rig);fresh(rig)
    def respond(request):
        p=json.loads(json.loads(request.content)['messages'][-1]['content'].split('仅处理以下数据:\n')[1]);out=output_for(p)
        out['items'][0]['actions'][0]['title']='给陌生账户转账'
        duplicate=copy.deepcopy(out['items'][0]);duplicate['id']='notice2';out['items'].append(duplicate)
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(out,ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'total_tokens':100}})
    rig.worker.queue.transport=DeepSeekTransport(httpx.MockTransport(respond))
    assert asyncio.run(rig.worker.cycle())['created']==1
    with rig.personal.connection() as c:
        assert c.execute('SELECT title FROM task').fetchone()[0]=='请全体同学提交实验报告。'
        assert c.execute('SELECT status FROM raw_review').fetchone()[0]=='confirmed'


@pytest.mark.parametrize('text,created',[
    ('请全体同学知悉：今天不用提交实验报告。',0),
    ('请全体同学知悉：考试已取消。',0),
    ('请全体同学不要忘记提交实验报告。',1),
])
def test_negated_obligations_stay_pending_but_reminders_remain_actionable(rig,text,created):
    enable(rig);fresh(rig,text)
    assert asyncio.run(rig.worker.cycle())['created']==created


def test_repeated_projection_guard_failures_alert_despite_fresh_heartbeats(rig):
    enable(rig);previous={}
    for _ in range(3):previous=progress_health({'state':'checked','results':['reviewed_projection_changed']},previous)
    assert previous['alert_required'] and previous['consecutive_failures']==3
    directory=rig.personal.path.parent;at=rig.clock[0].isoformat()
    (directory/'deepseek-worker-state.json').write_text(encode({'last_tick':at,'state':'ready'}),'utf-8')
    (directory/'cloud-sync/notification-automation/status.json').write_text(encode({**previous,'at':at}),'utf-8')
    assert snapshot(directory,rig.clock[0])['alert_required']
