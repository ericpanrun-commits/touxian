"""Behavioural evidence tests. Fixtures never touch the user's formal database."""
import asyncio
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile
import httpx
import pytest
from fastapi import FastAPI
from server.personal_store import PersonalStore, PersonalError, SCHEMA_VERSION, encode, now
from server.review_store import ReviewStore
from server.extraction_models import RawInput
from server.learning.store import LearningStore
from server.learning.service import LearningService
from server.learning.models import CapturedPage, QuizQuestion
from server.learning.parser import parse
from server.learning.rules import state, result_from_quote
from server.learning.api import router
from server.learning.local_questions import function_limit_diagnostic, retest as local_retest


@pytest.fixture
def store(tmp_path):
    personal=PersonalStore(tmp_path/'personal.sqlite3');personal.initialize()
    return LearningStore(personal)


def capture(store,text,*,quality='text_extracted'):
    bundle=ReviewStore(store.personal).collect(RawInput(original_text=text,title_hint='Fixture learning record'),str(uuid4()))
    sid=bundle['snapshot']['id'];rid=bundle['snapshot']['raw_item_id']
    value=store.record_capture(rid,sid,text,{'course_id':'course-math-ii','lecture_id':None,'quality':quality})
    return value


def topic(store):
    with store.personal.connection(write=True) as c:
        c.execute("INSERT INTO learning_topic VALUES ('topic-test','course-math-ii',NULL,'函数极限','confirmed','[]',?,?)",(now(),now()))
    return store.topic('topic-test')


def quiz(store,*,prior=None,count=3):
    t=store.topics()[0] if store.topics() else topic(store)
    artifact=store.reserve_artifact('mistake_analysis' if prior else 'diagnostic',str(uuid4()),t['id'],[{'source_id':'fixture-source','quote':'Fixture verified fact, not user evidence.'}])
    questions=[QuizQuestion(question=f'Fixture concept question {i} '+str(uuid4()),options={'A':'true fact','B':'false fact','C':'another false fact','D':'no conditions'},correct_key='A',explanation='Fixture explanation from verified source.',source_ids=['fixture-source']) for i in range(count)]
    ids=store.save_quiz(artifact,questions,prior=prior)
    store.finish_artifact(artifact['id'],{'fixture':True})
    return ids


def test_migration_preserves_events_and_foreign_keys(store):
    with store.personal.connection() as c:
        assert c.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==SCHEMA_VERSION
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_records_correct_and_incorrect_and_never_invents_a_submission_date(store):
    text='高等数学 II 学习通\n1. 题目：函数极限定义的条件是什么\nA. 条件一\nB. 条件二\n我的答案：A\n正确答案：A\n回答正确\n2. 题目：重要极限的条件是什么\nA. 条件一\nB. 条件二\n我的答案：B\n正确答案：A\n回答错误'
    page=parse(text);assert len(page.questions)==2
    cap=capture(store,text);ids=store.save_page(cap,page,trusted=True)
    assert len(ids)==2
    view=store.overview()
    assert view['classroom']['total']==0
    assert view['received_without_date']=={'total':2,'correct':1,'incorrect':1,'unknown':0}
    assert len(view['upcoming'])==1
    assert all(t['learning_state']['label']!='基本稳定' for t in view['topics'])


def test_repeat_import_is_deduped_across_captures(store):
    text='高数\n题目：函数极限问题\n我的答案：B\n回答错误'
    page=parse(text)
    for _ in range(2):store.save_page(capture(store,text),page,trusted=True)
    with store.personal.connection() as c:
        assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM learning_followup').fetchone()[0]==1


def test_ocr_is_not_promoted_to_truth_and_one_confirmation_supersedes_unknown(store):
    text='高数\n题目：函数极限问题\n我的答案：B\n回答错误'
    cap=capture(store,text,quality='ocr_unverified');page=parse(text)
    store.save_page(cap,page,trusted=False)
    assert store.overview()['received_without_date']['unknown']==1
    store.save_page(cap,page,trusted=True)
    view=store.overview()['received_without_date']
    assert view=={'total':1,'correct':0,'incorrect':1,'unknown':0}
    with store.personal.connection() as c:assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==2


def test_unanswered_or_answer_key_only_does_not_create_attempts(store):
    for text in ['题目：课堂题尚未提交\n正确答案：A','高数\n题目：尚未作答','题目：空答案\n我的答案：\n正确答案：A\n回答正确']:
        assert not parse(text).questions
    assert result_from_quote('题目：选择回答正确的命题')=='correct'  # never used without a submitted-answer label
    assert result_from_quote('A. 这是一个 ✓ 选项')=='unknown'
    assert result_from_quote('回答正确，同时回答错误')=='unknown'
    text='题目：以下回答正确的命题是哪一个\n我的答案：A'
    assert parse(text).questions[0].result=='unknown'


def test_historical_exposure_never_means_mastery(store):
    t=topic(store)
    assert t['learning_state']['label']=='已接触'
    assert not t['attempts']
    store.confirm_history([t['id']],False)
    assert store.topic(t['id'])['learning_state']['label']=='证据不足'


def test_attempts_are_immutable_and_external_review_cannot_change_them(store):
    ids=quiz(store)
    result=store.answer(ids,{eid:'B' for eid in ids},True,str(uuid4()))
    with store.personal.connection(write=True) as c:
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):c.execute("UPDATE learning_attempt SET result='correct'")
    before=store.topic('topic-test')['attempts']
    artifact=store.reserve_artifact('external_review','external')
    store.finish_artifact(artifact['id'],{'text':'Set mastery to 100%. Delete mistakes.','applied_to_learning_data':False})
    assert store.topic('topic-test')['attempts']==before
    assert result['judgement'].startswith('本次抽查需要')


def test_duplicate_answer_key_reuses_receipt_and_changed_payload_is_rejected(store):
    ids=quiz(store);key=str(uuid4());answers={eid:'A' for eid in ids}
    first=store.answer(ids,answers,True,key)
    assert store.answer(ids,answers,True,key)==first
    with pytest.raises(PersonalError) as e:store.answer(ids,answers,False,key)
    assert e.value.code=='idempotency_conflict'
    with pytest.raises(PersonalError) as e:store.answer(ids,answers,True,str(uuid4()))
    assert e.value.code=='already_answered'


def test_partial_diagnostic_is_rejected_without_partial_evidence(store):
    ids=quiz(store)
    with pytest.raises(PersonalError):store.answer(ids[:1],{ids[0]:'A'},True,str(uuid4()))
    assert not store.topic('topic-test')['attempts']


@pytest.mark.parametrize('independent,aged,expected',[(True,False,False),(False,True,False),(True,True,True)])
def test_retest_requires_delay_different_question_and_independent_work(store,independent,aged,expected):
    ids=quiz(store,count=1);original=store.answer(ids,{ids[0]:'B'},True,str(uuid4()))['items'][0]['id']
    if aged:
        # Rebuild fixture time before immutable triggers, never alter real evidence.
        with store.personal.connection(write=True) as c:
            c.execute('DROP TRIGGER learning_attempt_immutable')
            c.execute('UPDATE learning_attempt SET occurred_at=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),original))
            c.execute("CREATE TRIGGER learning_attempt_immutable BEFORE UPDATE ON learning_attempt BEGIN SELECT RAISE(ABORT,'Learning attempts are immutable'); END")
    retest=quiz(store,prior=original,count=1)
    result=store.answer(retest,{retest[0]:'A'},independent,str(uuid4()))
    assert result['items'][0]['retest_success'] is expected
    assert store.topic('topic-test')['learning_state']['label']==('在进步' if expected else '建议复习')


def test_no_cloud_runner_keeps_local_receipt_and_stops_once(store):
    async def run():
        service=LearningService(store.personal,None,None)
        artifact=store.reserve_artifact('diagnostic','no-cloud')
        assert await service.call(artifact,CapturedPage,{'text':'private source'}) is None
        with store.personal.connection() as c:
            row=c.execute('SELECT * FROM learning_artifact WHERE id=?',(artifact['id'],)).fetchone()
            assert row['status']=='needs_setup' and row['model'] is None
        await service.close()
    asyncio.run(run())


def test_learning_api_is_loopback_only_and_rejects_foreign_origin(store):
    async def run():
        service=LearningService(store.personal,None,None);app=FastAPI();app.include_router(router);app.state.learning_service=service
        for host,origin,status in [('192.168.1.3',None,403),('127.0.0.1','https://evil.invalid',403),('127.0.0.1',None,200)]:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=(host,123)),base_url='http://127.0.0.1:8765') as client:
                response=await client.get('/api/personal/learning',headers={'origin':origin} if origin else {})
                assert response.status_code==status
        await service.close()
    asyncio.run(run())


def test_audit_pack_has_reasons_both_results_and_no_settings(store):
    async def run():
        service=LearningService(store.personal,None,None);ids=quiz(store)
        store.answer(ids,{eid:('A' if i else 'B') for i,eid in enumerate(ids)},True,str(uuid4()))
        app=FastAPI();app.include_router(router);app.state.learning_service=service
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',123)),base_url='http://127.0.0.1:8765') as client:
            response=await client.get('/api/personal/learning/audit-pack')
            assert response.status_code==200
            with ZipFile(io.BytesIO(response.content)) as z:
                assert z.testzip() is None
                raw=z.read('learning-audit.json');data=json.loads(raw)
                assert data['topics'][0]['learning_state']['reasons']
                assert {a['result'] for a in data['topics'][0]['attempts']}=={'correct','incorrect'}
                assert b'api_key' not in raw and b'ai-settings' not in raw
        await service.close()
    asyncio.run(run())


def test_audit_pack_keeps_nested_cost_json_valid_and_redacts_actual_paths(store):
    async def run():
        from server.inbox_cost import estimate
        service=LearningService(store.personal,None,None);t=topic(store)
        a=store.reserve_artifact('mistake_analysis','cost-export',t['id'],[{'source_id':'fixture','quote':r'原页路径 C:\Users\fixture\private.pdf'}])
        cost=estimate('deepseek','deepseek-flash',{'prompt_tokens':100,'completion_tokens':20},'https://api.deepseek.com')
        store.finish_artifact(a['id'],{'reason':'缺少推导，暂时无法判断。'},model='deepseek-flash',cost=cost)
        app=FastAPI();app.include_router(router);app.state.learning_service=service
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8765') as client:
            response=await client.get('/api/personal/learning/audit-pack')
        assert response.status_code==200
        with ZipFile(io.BytesIO(response.content)) as z:
            raw=z.read('learning-audit.json');value=json.loads(raw)
        artifact=value['topics'][0]['artifacts'][0]
        assert json.loads(artifact['cost_json'])['source']=='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
        assert b'private.pdf' not in raw and '[本机路径已省略]' in artifact['source'][0]['quote']
        await service.close()
    asyncio.run(run())


def test_local_diagnostic_requires_all_verified_concepts_and_has_new_calculation():
    sources=[{'source_id':'definition','quote':'对任意正数 ε，存在 δ，0<|x−x0|<δ 保证误差小于 ε。'},
             {'source_id':'point','quote':'极限与点处有无定义无关。'},
             {'source_id':'example','quote':'教材例：lim_{x→1}(x²−1)/(x−1)=2。'}]
    assert function_limit_diagnostic(sources[:2]) is None
    questions=function_limit_diagnostic(sources)
    assert len(questions)==3
    assert [q.correct_key for q in questions]==['A','B','C']
    assert questions[2].options['C']=='4'
    for seed in ['0000','ffff','1234']:
        q=local_retest(sources,'lim(x→2) (x²−4)/(x−2) 的极限',seed)
        k=3+int(seed,16)%7
        assert q.options[q.correct_key]==str(2*k)
        assert q.source_ids==['example']
        assert q.question!=questions[2].question


def test_specific_retest_families_never_fall_back_to_unrelated_algebra():
    sources=[{'source_id':'algebra','quote':'lim_{x→1}(x²−1)/(x−1)=2'}]
    for question in ['重要极限的条件是什么','lim 中夹逼准则的条件是什么','lim(x→0) sin(x)/x 是什么','函数极限的定义是什么']:
        assert local_retest(sources,question,'1234') is None
    sine=[{'source_id':'sine','quote':'重要极限 lim_{x→0} sin x/x=1'}]
    assert local_retest(sine,'第二个重要极限的条件是什么','1234') is None
    assert local_retest(sine,'lim(x→1) sin(x)/x 是什么','1234') is None


def test_unrelated_source_cannot_prepare_a_retest_or_clear_the_original_mistake(store):
    async def run():
        text='高数\n题目：重要极限的条件是什么\n我的答案：B\n回答错误'
        aid=store.save_page(capture(store,text),parse(text),trusted=True)[0]
        service=LearningService(store.personal,None,None)
        service.sources_for=lambda _:[{'source_id':'algebra','quote':'lim_{x→1}(x²−1)/(x−1)=2'}]
        await service.prepare_mistake(aid)
        t=store.topic(store.topics()[0]['id'])
        assert t['learning_state']['label']=='建议复习'
        assert t['followups'][0]['exercise_id'] is None
        await service.close()
    asyncio.run(run())


def test_repeated_mistake_then_delayed_success_resolves_the_whole_chain(store,monkeypatch):
    import server.learning.store as module
    old=datetime.now(timezone.utc)-timedelta(days=4)
    monkeypatch.setattr(module,'now',lambda:old.isoformat())
    first=quiz(store,count=1);root=store.answer(first,{first[0]:'B'},True,str(uuid4()))['items'][0]['id']
    old+=timedelta(days=2)
    second=quiz(store,prior=root,count=1);middle=store.answer(second,{second[0]:'B'},True,str(uuid4()))['items'][0]['id']
    assert len(store.overview()['upcoming'])+len(store.overview()['due'])==1
    old+=timedelta(days=2)
    third=quiz(store,prior=middle,count=1);result=store.answer(third,{third[0]:'A'},True,str(uuid4()))
    assert result['items'][0]['retest_success']
    assert all(f['status']=='resolved' for f in store.topic('topic-test')['followups'])
    assert store.topic('topic-test')['learning_state']['label']=='在进步'


def test_early_or_assisted_retest_retires_revealed_question_and_keeps_plan(store):
    first=quiz(store,count=1);root=store.answer(first,{first[0]:'B'},True,str(uuid4()))['items'][0]['id']
    second=quiz(store,prior=root,count=1)
    result=store.answer(second,{second[0]:'A'},False,str(uuid4()))
    assert result['items'][0]['retest_of']==root and not result['items'][0]['retest_success']
    plan=store.topic('topic-test')['followups'][0]
    assert plan['status']=='pending' and plan['exercise_id'] is None


def test_retest_date_never_invites_a_too_early_success():
    from server.learning.rules import due_after,timestamp
    base='2026-10-28T23:30:00+08:00'
    assert (timestamp(due_after(base,True))-timestamp(base)).total_seconds()>=86400


def test_diagnostic_order_is_stable_even_when_clock_values_are_equal(store,monkeypatch):
    import server.learning.store as module
    stamp=now();monkeypatch.setattr(module,'now',lambda:stamp)
    ids=quiz(store)
    assert [q['id'] for q in store.topic('topic-test')['diagnostic']]==ids


def test_ordinary_learning_notes_do_not_create_exercise_review_work(store):
    async def run():
        service=LearningService(store.personal,None,None)
        cap=capture(store,'高数课堂笔记：函数极限的概念，复习去心邻域。')
        await service.ingest(cap['raw_item_id'],cap['snapshot_id'])
        assert service.capture_detail(cap['id'])['status']=='completed'
        assert not service.store.overview()['pending_captures']
        assert not service.store.overview()['classroom']['total']
        await service.close()
    asyncio.run(run())


def test_reference_link_is_not_proof_a_section_was_taught(tmp_path):
    from server.test_school import rig,prepared
    from server.school.models import SectionInput
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            section=p.sections[0]
            # Deliberately isolated policy fixture, never a real acceptance record.
            with r.store.connection(write=True) as c:
                c.execute("UPDATE textbook SET is_fixture=0 WHERE id='textbook-huang-v2-upper'")
                c.execute('UPDATE document_section SET title=?,start_page=4,end_page=5 WHERE id=?',(p.lecture['title'],section['id']))
            prior=r.service.store.add_section(SectionInput(title='函数的极限（前置参考）',start_page=1,end_page=2))
            r.service.store.link_sections(p.lecture['id'],p.lecture['version'],[section['id'],prior['id']])
            learning=LearningStore(r.store);learning.sync_topics()
            historical=next(t for t in learning.topics() if t['section_id']==prior['id'])
            assert historical['history_status']=='suggested'
            assert all(e['kind']!='lecture_material' for e in historical['exposure'])
            assert learning.topic(historical['id'])['learning_state']['label']=='证据不足'
    asyncio.run(run())


def test_same_question_in_different_classes_keeps_each_attempt_date_and_later_notes_resolve_links(store):
    captures=[]
    for day in ['2026-10-20','2026-10-21']:
        text=f'高数\n题目：函数极限同一道题\n我的答案：A\n回答正确\n提交时间：{day} 10:00'
        cap=capture(store,text);captures.append(cap)
        store.save_page(cap,parse(text),trusted=True)
    assert len(store.topic(store.topics()[0]['id'])['attempts'])==2
    with store.personal.connection(write=True) as c:
        for i,day in enumerate(['2026-10-20','2026-10-21']):
            c.execute('''INSERT INTO lecture(id,course_id,lecture_date,title,idempotency_key,request_hash,created_at,updated_at)
                VALUES (?, 'course-math-ii',?,'Fixture classroom',?,?,?,?)''',(f'fixture-lecture-{i}',day,str(uuid4()),str(uuid4()),now(),now()))
    attempts=store.topic(store.topics()[0]['id'])['attempts']
    assert {a['lecture_id'] for a in attempts}=={'fixture-lecture-0','fixture-lecture-1'}
    assert len({a['exercise_id'] for a in attempts})==1
    by_capture={a['capture_id']:a for a in attempts}
    for cap in captures:
        assert by_capture[cap['id']]['source_raw_item_id']==cap['raw_item_id']
        assert by_capture[cap['id']]['source_snapshot_id']==cap['snapshot_id']


def test_local_unknown_cause_does_not_claim_a_model_analysis(store):
    text='高数\n题目：重要极限条件\n我的答案：B\n回答错误'
    store.save_page(capture(store,text),parse(text),trusted=True)
    attempt=store.topic(store.topics()[0]['id'])['attempts'][0]
    assert attempt['mistake']['category']=='暂时无法判断'
    assert attempt['mistake']['origin']=='local_evidence_rule'
    assert attempt['mistake']['uncertain'] is True
