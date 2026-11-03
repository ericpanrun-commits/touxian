"""Synthetic preview -> notes -> review -> independent retest; never formal student data."""
import asyncio
import copy
import io
import json
from uuid import uuid4
from zipfile import ZipFile
import httpx
import pytest
from fastapi import FastAPI
from server.personal_store import now,encode
from server.extraction_models import RawInput
from server.review_store import ReviewStore
from server.learning.service import LearningService
from server.learning.store import LearningStore
from server.learning.activities import activity_context,before_lecture,activity_due,related_topic
from server.learning.parser import parse
from server.learning.api import router
from server.school.validation import validate_review
from server.school.provider import SchoolProvider
from server.ai_settings import AIConfig
from server.ai_transport import DeepSeekTransport
from pydantic import SecretStr
from server.extraction_errors import ExtractionFailure
from server.test_learning import store,quiz
from server.test_school import rig,prepared,generate_input,finish,review_output,intake
from server.test_documents import text_pdf


def preview_text(*,count=5,right=3,day='2026-10-25',lesson='2026-10-26',task='重要极限预习',identity='fixture-preview'):
    lines=['高等数学 II 学习通 · 隔离预习测试',f'预习任务：{task}',f'任务ID：{identity}']
    if lesson:lines.append('上课日期：'+lesson)
    lines.extend(['阅读材料：极限的适用条件 · 已阅读','视频：条件检查示范 · 已观看'])
    if day:lines.append(f'提交时间：{day} 18:00:00')
    lines.extend(f'{i+1}. 题目：极限条件检查，测试情形{i+1}\nA. 满足给定条件\nB. 忽略条件\n我的答案：'+('A' if i<right else 'B')+'\n正确答案：A\n回答'+('正确' if i<right else '错误') for i in range(count))
    return '\n'.join(lines)


async def ingest(service,text,*,title='学习通预习任务'):
    bundle=ReviewStore(service.personal).collect(RawInput(original_text=text,title_hint=title),str(uuid4()))
    rid=bundle['snapshot']['raw_item_id'];sid=bundle['snapshot']['id']
    await service.ingest(rid,sid)
    if service.tasks:await asyncio.gather(*list(service.tasks.values()))
    with service.personal.connection() as c:
        cap=c.execute('SELECT id FROM learning_capture WHERE raw_item_id=? AND snapshot_id=?',(rid,sid)).fetchone()
    return service.capture_detail(cap[0])


def add_lecture(store,day='2026-10-26',lid='fixture-next-lecture',title='重要极限与条件'):
    with store.personal.connection(write=True) as c:
        c.execute('''INSERT INTO lecture(id,course_id,lecture_date,title,idempotency_key,request_hash,created_at,updated_at)
            VALUES (?,'course-math-ii',?,?,?,?,?,?)''',(lid,day,title,str(uuid4()),str(uuid4()),now(),now()))
    return lid


def test_preview_five_exercises_keep_both_results_and_two_followups(store):
    async def run():
        service=LearningService(store.personal,None,None)
        cap=await ingest(service,preview_text())
        view=store.overview();session=view['preview']
        assert session['exercises']=={'total':5,'correct':3,'incorrect':2,'unknown':0}
        assert session['material_exposures']==2 and session['material_completed']==2
        assert view['classroom']['total']==0 and session['weaknesses'][0]['incorrect']==2
        with store.personal.connection() as c:
            assert c.execute('SELECT count(*) FROM learning_exercise').fetchone()[0]==5
            assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==5
            assert c.execute('SELECT count(*) FROM learning_followup').fetchone()[0]==2
        assert cap['context']['activity_type']=='preview'
        assert store.topic(store.topics()[0]['id'])['learning_state']['label']=='不稳定'
        await service.close()
    asyncio.run(run())


def test_material_completion_is_exposure_and_never_an_attempt_or_mastery(store):
    async def run():
        service=LearningService(store.personal,None,None)
        await ingest(service,preview_text(count=0))
        view=store.overview()
        assert view['preview']['exercises']['total']==0 and view['preview']['material_exposures']==2
        assert all(t['learning_state']['label']=='已接触' for t in view['topics'])
        with store.personal.connection() as c:assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==0
        await service.close()
    asyncio.run(run())


def test_material_and_result_pages_of_same_named_task_form_one_session(store):
    async def run():
        service=LearningService(store.personal,None,None)
        await ingest(service,preview_text(count=0))
        text=preview_text().replace('阅读材料：极限的适用条件 · 已阅读\n视频：条件检查示范 · 已观看\n','')
        await ingest(service,text)
        view=store.overview()
        assert len(view['preview_sessions'])==1
        assert view['preview']['exercises']['total']==5 and view['preview']['material_exposures']==2
        assert len(view['preview']['capture_ids'])==2
        await service.close()
    asyncio.run(run())


def test_preview_and_optional_classroom_share_exercise_but_not_attempt_context(store,monkeypatch):
    import server.learning.store as module
    monkeypatch.setattr(module,'now',lambda:'2026-10-25T23:00:00+08:00')
    async def run():
        service=LearningService(store.personal,None,None)
        text=preview_text(count=1,right=1)
        await ingest(service,text)
        await ingest(service,text) # repeat receipt is not another attempt
        # Keep the exact same question and options in another activity.
        classroom='高数 学习通 课堂题\n提交时间：2026-10-25 18:00:00\n1. 题目：'+text.split('1. 题目：',1)[1]
        await ingest(service,classroom,title='课堂即时练习')
        with store.personal.connection() as c:
            assert c.execute('SELECT count(*) FROM learning_exercise').fetchone()[0]==1
            aa=store.active_attempts(c)
        assert len(aa)==2 and {a['activity_type'] for a in aa}=={'preview','classroom'}
        assert store.overview()['preview']['exercises']['total']==1
        assert store.overview()['classroom']['total']==1
        await service.close()
    asyncio.run(run())


def test_question_wording_cannot_turn_classroom_question_into_preview():
    text='高数 课堂题\n1. 题目：预习已完成是否证明掌握\n我的答案：B\n回答正确'
    assert activity_context(text,'课堂结果','fixture','course-math-ii')['activity_type']=='classroom'
    assert not parse(text).preview_completed_quote
    assert not parse('高数 课前预习\n阅读材料：尚待阅读 · 未完成').preview_materials[0].completed


def test_preview_does_not_attach_to_submission_day_and_later_notes_resolve_next_day(store):
    async def run():
        service=LearningService(store.personal,None,None)
        add_lecture(store,day='2026-10-25',lid='wrong-same-day')
        cap=await ingest(service,preview_text(lesson=None))
        with store.personal.connection() as c:assert all(a['lecture_id'] is None for a in store.active_attempts(c))
        lid=add_lecture(store)
        with store.personal.connection() as c:
            aa=store.active_attempts(c)
            assert all(a['lecture_id']==lid and a['lecture_link_basis']=='inferred_unique_next_day' for a in aa)
        evidence=before_lecture(store.personal,lid)
        assert evidence['counts']=={'total':5,'correct':3,'incorrect':2,'unknown':0}
        assert {a['raw_item_id'] for a in evidence['attempts']}=={cap['raw_item_id']}
        add_lecture(store,lid='second-same-day')
        with store.personal.connection() as c:assert all(a['lecture_id'] is None for a in store.active_attempts(c))
        await service.close()
    asyncio.run(run())


def test_receipt_time_does_not_invent_preview_to_lecture_link(store):
    async def run():
        service=LearningService(store.personal,None,None)
        add_lecture(store)
        await ingest(service,preview_text(day=None,lesson=None))
        with store.personal.connection() as c:assert all(a['lecture_id'] is None for a in store.active_attempts(c))
        assert store.overview()['preview']['lecture'] is None
        await service.close()
    asyncio.run(run())


def test_explicit_late_lecture_keeps_retest_after_that_class():
    due=activity_due('2026-10-20T18:00:00+08:00',{'activity_type':'preview','target_lecture_date':'2026-10-28'})
    assert due=='2026-10-29T01:00:00+00:00'


def test_lecture_review_reads_actual_preview_and_saves_immutable_version_context(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            before=r.service.contexts.build(p.lecture['id'])
            service=LearningService(r.store,None,None)
            await ingest(service,preview_text())
            context=r.service.contexts.build(p.lecture['id'])
            assert before['context_id']!=context['context_id']
            assert context['before_lecture']['counts']=={'total':5,'correct':3,'incorrect':2,'unknown':0}
            request=await r.service.generate(p.lecture['id'],generate_input(p.lecture,use_vision=False))
            await finish(r.service)
            job=r.service.jobs.get(request['job']['id'])
            assert job['status']=='completed',job
            artifact=r.service.artifacts.get(job['result']['artifact_id'])
            assert artifact['content']['before_lecture']==context['before_lecture']
            assert artifact['input_versions']['before_lecture']==context['before_lecture']
            saved=copy.deepcopy(artifact['content'])
            await ingest(service,preview_text(count=1,right=0,identity='second-preview-task'))
            latest=r.service.artifacts.get(artifact['id'])
            assert latest['content']==saved and latest['preview_evidence_changed']
            forged=review_output(context);forged['before_lecture']=copy.deepcopy(context['before_lecture'])
            forged['before_lecture']['counts']['correct']=5
            with pytest.raises(ExtractionFailure,match='课前预习'):validate_review(encode(forged),context)
            await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('after,independent,expected',[('2026-10-27T18:00:00+08:00',True,True),('2026-10-27T18:00:00+08:00',False,False),('2026-10-25T20:00:00+08:00',True,False)])
def test_before_vs_after_requires_notes_and_real_delayed_independent_retest(tmp_path,monkeypatch,after,independent,expected):
    import server.learning.store as module
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            service=LearningService(r.store,None,None)
            await ingest(service,preview_text(count=1,right=0))
            store=service.store
            with store.personal.connection() as c:root=store.active_attempts(c)[0]['id']
            questions=quiz(store,prior=root,count=1)
            monkeypatch.setattr(module,'now',lambda:after)
            store.answer(questions,{questions[0]:'A'},independent,str(uuid4()))
            with store.personal.connection() as c:
                aa=store.active_attempts(c)
                a=next(a for a in aa if a['kind']=='retest')
                assert a['before_after_progress'] is expected
                assert a['origin_activity_type']=='preview' and a['lecture_id']==p.lecture['id']
            session=store.overview()['preview']
            assert session['after_lecture']['progress'] is expected
            await service.close()
    asyncio.run(run())


def test_audit_pack_contains_preview_material_attempts_and_session_context(store):
    async def run():
        service=LearningService(store.personal,None,None)
        await ingest(service,preview_text())
        app=FastAPI();app.include_router(router);app.state.learning_service=service
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8765') as client:
            response=await client.get('/api/personal/learning/audit-pack')
        with ZipFile(io.BytesIO(response.content)) as z:data=json.loads(z.read('learning-audit.json'))
        session=data['preview_sessions'][0]
        assert session['exercises']['total']==5 and session['material_exposures']==2
        assert sum(len(t['attempts']) for t in data['topics'])==5
        assert all(a['activity_type']=='preview' for t in data['topics'] for a in t['attempts'])
        await service.close()
    asyncio.run(run())


def test_ocr_preview_results_wait_for_one_page_confirmation(store):
    async def run():
        service=LearningService(store.personal,None,None);text=preview_text()
        bundle=ReviewStore(store.personal).collect(RawInput(original_text=text,title_hint='预习结果 OCR 隔离测试'),str(uuid4()))
        rid=bundle['snapshot']['raw_item_id'];sid=bundle['snapshot']['id']
        context={'course_id':'course-math-ii','quality':'ocr_unverified',**activity_context(text,'',rid,'course-math-ii')}
        cap=store.record_capture(rid,sid,text,context);store.save_page(cap,parse(text),trusted=False)
        assert store.overview()['preview']['exercises']=={'total':5,'correct':0,'incorrect':0,'unknown':5}
        assert store.overview()['preview']['material_completed']==0
        await service.confirm_capture(cap['id'])
        assert store.overview()['preview']['exercises']=={'total':5,'correct':3,'incorrect':2,'unknown':0}
        assert store.overview()['preview']['material_completed']==2
        with store.personal.connection() as c:assert len(store.active_attempts(c))==5
        await service.close()
    asyncio.run(run())


def test_notes_intake_creates_next_lecture_and_links_existing_preview(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            service=LearningService(r.store,r.service,r.extract)
            await ingest(service,preview_text())
            assert service.store.overview()['preview']['lecture'] is None
            path=text_pdf(tmp_path/'高数课堂笔记2026-10-26 Apple Notes.pdf',['Important limit conditions in actual fixture notes.'])
            rid,doc=intake(r.service,path)
            with r.store.connection() as c:sid=c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=? ORDER BY created_at DESC LIMIT 1',(rid,)).fetchone()[0]
            await service.ingest(rid,sid)
            session=service.store.overview()['preview']
            assert session['lecture'] and session['lecture']['notes_available']
            assert session['lecture']['lecture_date']=='2026-10-26'
            assert before_lecture(r.store,session['lecture']['id'])['counts']['incorrect']==2
            await service.close()
    asyncio.run(run())


def test_after_lecture_progress_does_not_use_unrelated_class_notes(tmp_path,monkeypatch):
    import server.learning.store as module
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            with r.store.connection(write=True) as c:c.execute("UPDATE lecture SET title='连续性课堂' WHERE id=?",(p.lecture['id'],))
            r.service.learning.store_page(p.note['id'],1,{'extracted_text':'连续性定义与连续函数的性质。','metadata':{}},method='pypdf',quality='text_extracted')
            service=LearningService(r.store,None,None)
            await ingest(service,preview_text(count=1,right=0))
            with r.store.connection() as c:root=service.store.active_attempts(c)[0]['id']
            questions=quiz(service.store,prior=root,count=1)
            monkeypatch.setattr(module,'now',lambda:'2026-10-27T18:00:00+08:00')
            service.store.answer(questions,{questions[0]:'A'},True,str(uuid4()))
            with r.store.connection() as c:
                a=next(a for a in service.store.active_attempts(c) if a['kind']=='retest')
                assert a['retest_success'] and not a['before_after_progress']
            assert not service.store.overview()['preview']['after_lecture']['progress']
            await service.close()
    asyncio.run(run())


def test_late_preview_submission_is_not_presented_as_before_class(store):
    async def run():
        service=LearningService(store.personal,None,None);lid=add_lecture(store)
        await ingest(service,preview_text(day='2026-10-27',lesson='2026-10-26'))
        assert store.overview()['preview']['exercises']['total']==5
        assert before_lecture(store.personal,lid)['counts']['total']==0
        await service.close()
    asyncio.run(run())


def test_session_with_conflicting_lecture_dates_does_not_pick_last_capture(store):
    async def run():
        service=LearningService(store.personal,None,None)
        add_lecture(store);add_lecture(store,day='2026-10-27',lid='other-lecture')
        await ingest(service,preview_text(count=1,lesson='2026-10-26'))
        await ingest(service,preview_text(count=1,lesson='2026-10-27'))
        assert len(store.overview()['preview_sessions'])==1
        assert store.overview()['preview']['lecture'] is None
        await service.close()
    asyncio.run(run())


def test_real_provider_payload_reads_preview_without_sending_file_paths(store):
    async def run():
        service=LearningService(store.personal,None,None);lid=add_lecture(store)
        await ingest(service,preview_text())
        preview=before_lecture(store.personal,lid)
        preview['attempts'][0]['question']+=' C:\\private\\notes.pdf\npassword=fixture-password'
        sent=[]
        def handle(req):
            sent.append(json.loads(req.content))
            return httpx.Response(200,json={'model':'deepseek-flash','choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1}})
        provider=SchoolProvider(DeepSeekTransport(httpx.MockTransport(handle)))
        context={'lecture':{'id':lid},'mistakes':{'available':False},'before_lecture':preview}
        await provider.compose({'facts':[]},context,AIConfig(),SecretStr('fixture-key'),lambda:None)
        payload=json.loads(sent[0]['messages'][1]['content'])
        assert payload['before_lecture']['counts']=={'total':5,'correct':3,'incorrect':2,'unknown':0}
        assert 'private' not in encode(sent) and 'fixture-password' not in encode(sent)
        assert payload['before_lecture']['weaknesses']
        await service.close()
    asyncio.run(run())


def test_class_title_alone_cannot_prove_related_note_content():
    assert not related_topic('两个重要极限使用条件',{'title':'重要极限课堂','notes_evidence_text':'连续性定义与连续函数'})
    assert not related_topic('两个重要极限使用条件',{'title':'重要极限课堂','notes_evidence_text':''})
    assert related_topic('两个重要极限使用条件',{'title':'高数笔记','notes_evidence_text':'Important limit conditions and radian requirement.'})


def test_missing_submission_date_keeps_preview_attempts_but_marks_timing_unknown(store):
    async def run():
        service=LearningService(store.personal,None,None);lid=add_lecture(store)
        await ingest(service,preview_text(day=None))
        result=before_lecture(store.personal,lid)
        assert result['timing_unknown']==5 and result['counts']['total']==5
        assert result['counts']['incorrect']==2
        with store.personal.connection() as c:assert not any(a['before_after_progress'] for a in store.active_attempts(c))
        await service.close()
    asyncio.run(run())


def test_one_successful_retest_keeps_other_preview_retest_pending(tmp_path,monkeypatch):
    import server.learning.store as module
    async def run():
        async with rig(tmp_path) as r:
            prepared(r.service,tmp_path);service=LearningService(r.store,None,None)
            await ingest(service,preview_text(count=2,right=0))
            with r.store.connection() as c:roots=service.store.active_attempts(c)
            questions=quiz(service.store,prior=roots[0]['id'],count=1)
            monkeypatch.setattr(module,'now',lambda:'2026-10-27T18:00:00+08:00')
            service.store.answer(questions,{questions[0]:'A'},True,str(uuid4()))
            after=service.store.overview()['preview']['after_lecture']
            assert after['progress'] and after['independent_successes']==1 and after['pending']==1
            await service.close()
    asyncio.run(run())
