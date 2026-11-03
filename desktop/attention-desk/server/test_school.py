"""One-course evidence pipeline. Synthetic textbook fixtures are never formal data."""
import asyncio
import copy
import hashlib
import io
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile
import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from pypdf import PdfWriter
from PIL import Image
from server.personal_store import PersonalStore, PersonalError, encode, now
from server.documents.service import DocumentService
from server.extraction_service import ExtractionService
from server.extraction_errors import ExtractionFailure
from server.extraction_provider import ProviderResult
from server.extraction_models import ConfirmationInput
from server.inbox_store import InboxStore
from server.review_store import ReviewStore
from server.test_extraction_review import TestSettings
from server.test_documents import text_pdf
from server.school.service import SchoolService
from server.school.models import LectureInput, SectionInput, GenerateInput, VisionInput, FeedbackInput
from server.school.pdf_pages import catalog, parse_page
from server.school.validation import validate_review
from server.school.documents import digest
from server.school.export import context_pack
from server.school.proposals import stage_proposals
from server.school.api import router
from server.school.protocol import catalog as evidence_catalog

BOOK_PAGES = [
    'Squeeze theorem compares lower upper bounds. Condition same common limit and ordered functions. Squeeze proof.',
    'Important sine limit sin x over x equals one near zero. Radian condition. Sine substitution example.',
    'Important exponential limit approaches Euler constant e. Power expression and exponential substitution.',
    'Sequence definition epsilon N. Monotone bounded convergence. Sequence arithmetic and boundedness.',
    'Continuity definition at a point. Continuous function and composition.',
]
QUERIES = [('squeeze theorem',1),('lower upper bounds',1),('ordered functions',1),('squeeze proof',1),('夹逼准则条件',1),
           ('sine limit',2),('radian condition',2),('sine substitution example',2),('弧度',2),('sin x over x',2),
           ('exponential limit',3),('Euler constant',3),('Power expression',3),('exponential substitution',3),
           ('Sequence definition epsilon',4),('Monotone bounded convergence',4),('Sequence arithmetic',4),('数列定义',4),
           ('Continuity definition',5),('Continuous function composition',5)]


def review_output(context):
    note=next(s for s in context['sources'] if s['source_kind']=='notes' and s['kind']=='text')
    book=next(s for s in context['sources'] if s['source_kind']=='textbook')
    def cite(s):return {'source_id':s['source_id'],'quote':s['text'][:100]}
    def claim(text,s=book):return {'text':text,'provenance':s['source_kind'] if s['quality'] not in {'ocr_unverified','vision_uncertain'} else 'uncertain','citations':[cite(s)]}
    return {'schema_version':'lecture-review-v1',
        'core_concepts':[{'name':name,'explanation':claim(name)} for name in ['夹逼准则','比较条件','共同极限']],
        'notes_taught':[claim('当前笔记记录了极限相关内容。',note)],
        'must_master':[claim('比较上下界及共同趋近值。')],
        'method_cards':[{'signal':'函数处于两个边界之间','method':'检查夹逼准则','conditions':'上下界具有共同极限','common_misuse':'忽略有序关系','citations':[cite(book)]}],
        'textbook_supplement':[{'text':'当前可识别笔记未见上下界条件的完整表述。','provenance':'textbook','citations':[cite(book)],'notes_presence':'not_found_in_provided_evidence'}],
        'understanding_gaps':[], 'connections':[],
        'mistakes':{'history_available':False,'message':'当前没有足够历史错误数据','items':[]},
        'self_tests':[{'question':f'自测 {i+1}：使用比较方法时需检查什么？','answer':'检查有序关系和共同极限。','citations':[cite(book)]} for i in range(3)],
        'suggested_actions':[{'text':'用 10 分钟复习极限条件','duration_minutes':10,'rationale':claim('核对比较条件有助于使用方法。')}]}


def evidence_selection(context):
    facts=evidence_catalog(context)
    return {'notes_facts':[f['fact_id'] for f in facts if f['source_kind']=='notes' and not f['uncertain']],
        'textbook_facts':[f['fact_id'] for f in facts if f['source_kind']=='textbook' and not f['uncertain']],
        'formulas':[f['fact_id'] for f in facts if f['math_bearing']],
        'conditions':[f['fact_id'] for f in facts if f['condition_bearing']],
        'examples':[],'uncertain_items':[f['fact_id'] for f in facts if f['uncertain']]}


def composition_output(evidence,context):
    note=next(f for f in evidence['facts'] if f['source_kind']=='notes')
    book=next(f for f in evidence['facts'] if f['source_kind']=='textbook' and not f['uncertain'])
    def claim(text,f=book):
        return {'text':text,'provenance':'uncertain' if f['uncertain'] else f['source_kind'],'fact_ids':[f['fact_id']]}
    return {'core_concepts':[{'name':name,'explanation':claim('核对对应证据：[['+book['fact_id']+']]')} for name in ['比较准则','适用条件','共同趋向']],
        'notes_taught':[claim('当前笔记记录的内容：[['+note['fact_id']+']]',note)],
        'must_master':[claim('核对方法的前提。')],
        'method_cards':[{'signal':'对象处于两个边界之间','method':'按原文核对比较步骤。','conditions':'原文条件：[['+book['fact_id']+']]',
            'common_misuse':'忽略有序关系和共同趋向。','fact_ids':[book['fact_id']]}],
        'textbook_supplement':[claim('教材给出的条件可供对照。')],
        'understanding_gaps':[],'connections':[],
        'self_tests':[{'question':'自测'+name+'：使用比较方法前需检查什么？','answer':'对照原文检查：[['+book['fact_id']+']]','fact_ids':[book['fact_id']]} for name in ['一','二','三']],
        'suggested_actions':[{'text':'复习比较方法的前提。','duration_minutes':10,'rationale':claim('用对应证据核对条件。')}]}


class SchoolFixtureProvider:
    def __init__(self):self.calls=[];self.failure=None;self.clarity='clear';self.bad_review=False
    async def extract(self,context,config,key,gate):
        gate();self.calls.append('evidence')
        if self.failure:raise self.failure
        return ProviderResult(encode(evidence_selection(context)),{'prompt_tokens':1000,'completion_tokens':200},'deepseek-flash')
    async def compose(self,evidence,context,config,key,gate):
        gate();self.calls.append('composition')
        if self.failure:raise self.failure
        output=composition_output(evidence,context)
        if self.bad_review:output['core_concepts'][0]['explanation']['text']='老师没有讲夹逼准则'
        return ProviderResult(encode(output),{'prompt_tokens':1500,'completion_tokens':2000},'deepseek-flash')
    async def repair(self,*args):
        gate=args[-1];gate();self.calls.append('repair')
        raise ExtractionFailure('fixture_repair_unexpected','正常测试不应需要定向修复。')
    async def vision(self,pages,config,key,gate):
        gate();self.calls.append(('vision',len(pages)))
        if self.failure:raise self.failure
        return ProviderResult(encode({'pages':[{'source_id':p['source_id'],'transcript':'Squeeze theorem compares bounds. Important limit conditions recorded in notes.',
            'clarity':self.clarity,'uncertainties':[] if self.clarity=='clear' else ['公式待核对']} for p in pages]}),
            {'prompt_tokens':1000,'completion_tokens':500},'deepseek-flash')


def intake(service,path):
    result=service.documents.files.collect([(path.name,'application/pdf',path)],'',path.stem,str(uuid4()))
    raw_id=result['snapshot']['raw_item_id']
    InboxStore(service.personal).link(raw_id,1,'course-math-ii','')
    doc=service.learning.register(result['snapshot']['attachments'][0]['id'])
    service.learning.catalog(doc['id'],catalog(path))
    return raw_id,service.learning.get(doc['id'])


def prepared(service,root,*,book=True,notes_quality='text_extracted'):
    note_path=text_pdf(root/'notes.pdf',['Squeeze theorem and important limit conditions in classroom notes.'])
    raw,note=intake(service,note_path)
    service.learning.store_page(note['id'],1,{'extracted_text':'Squeeze theorem and important limit conditions in classroom notes.','metadata':{}},method='pypdf',quality=notes_quality)
    lecture=service.store.create_lecture(LectureInput(title='夹逼准则与重要极限',lecture_date='2026-10-26',notes_raw_item_id=raw),str(uuid4()))
    sections=[]
    if book:
        path=text_pdf(root/'fixture-textbook-NOT-FORMAL.pdf',BOOK_PAGES)
        braw,doc=intake(service,path)
        service.store.link_book(braw,1,'chapter_scan',fixture=True)
        for index,text in enumerate(BOOK_PAGES,1):
            service.learning.store_page(doc['id'],index,{'extracted_text':text,'metadata':{}},method='pypdf',quality='text_extracted')
        section=service.store.add_section(SectionInput(title='Fixture limits and continuity',start_page=1,end_page=5))
        sections.append(section)
        lecture=service.store.link_sections(lecture['id'],lecture['version'],[section['id']])
    return SimpleNamespace(lecture=lecture,note=note,raw_id=raw,sections=sections)


@asynccontextmanager
async def rig(root,*,fixture_mode=True):
    root.mkdir(exist_ok=True)
    store=PersonalStore(root/'personal.sqlite3');store.initialize()
    docs=DocumentService(store);extract=ExtractionService(store,TestSettings());provider=SchoolFixtureProvider()
    service=SchoolService(store,docs,extract,provider=provider,fixture_mode=fixture_mode)
    try:yield SimpleNamespace(store=store,docs=docs,extract=extract,provider=provider,service=service)
    finally:
        await service.close();await extract.close();await asyncio.to_thread(docs.close)


async def finish(service):
    if service.tasks:await asyncio.gather(*list(service.tasks.values()))
    if service.futures:await asyncio.gather(*(asyncio.wrap_future(f) for f in list(service.futures.values())))


def generate_input(lecture,**kwargs):
    return GenerateInput(expected_version=lecture['version'],request_id=str(uuid4()),confirm_cost=True,**kwargs)


def test_textbook_metadata_and_lecture_real_date_no_inferred_capture_date(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path,book=False)
            book=r.service.store.book()
            assert (book['title'],book['editor'],book['publisher'],book['isbn'])==('高等数学（第二版）（上）','黄立宏','北京大学出版社','9787301349656')
            assert book['status']=='waiting_file' and not book['document_id'] and book['real_acceptance']=='pending'
            assert p.lecture['lecture_date']=='2026-10-26' and p.lecture['course_id']=='course-math-ii'
            assert p.lecture['notes'][0]['id']==p.note['id']
            pack=r.service.contexts.build(p.lecture['id'],allow_incomplete=True)
            assert pack['missing'] and pack['mistakes']['message']=='当前没有足够历史错误数据'
            with pytest.raises(PersonalError,match='教材'):await r.service.generate(p.lecture['id'],generate_input(p.lecture))
            assert not r.provider.calls
    asyncio.run(run())


def test_native_pdf_catalog_large_book_labels_hash_idempotency(tmp_path,monkeypatch):
    path=text_pdf(tmp_path/'long.pdf',['Definition limit with text layer readable and sufficient length.']*205)
    assert catalog(path)=={'page_count':205,'labels':{},'catalog_only':True}
    def no_ocr(*a):raise AssertionError('text PDF must not call OCR')
    monkeypatch.setattr('server.school.pdf_pages.native_ocr',no_ocr)
    result=parse_page(path,205,tmp_path)
    assert result['metadata']['quality']=='text_extracted' and result['blocks'][0]['page']==205
    async def run():
        async with rig(tmp_path/'runtime') as r:
            raw,doc=intake(r.service,path)
            again=r.service.learning.register(doc['attachment_id'])
            assert again['id']==doc['id']
            pages=r.service.learning.pages(doc['id'],limit=2000)
            assert len(pages)==205 and len({p['page_hash'] for p in pages})==205
            assert pages[-1]['page_hash']==digest(doc['sha256']+':205')
            assert hashlib.sha256(r.docs.files.path(r.docs.files.get(doc['attachment_id'])).read_bytes()).hexdigest()==doc['sha256']
            r.service.learning.catalog(doc['id'],catalog(path))
            assert len(r.service.learning.pages(doc['id'],limit=2000))==205
            r.service.learning.store_page(doc['id'],205,result)
            with r.store.connection(write=True) as c:
                with pytest.raises(sqlite3.IntegrityError):c.execute("UPDATE document_block SET text='overwrite'")
    asyncio.run(run())


def test_lecture_duplicate_and_request_conflict_scope(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path,book=False)
            payload=LectureInput(title=p.lecture['title'],lecture_date='2026-10-26',notes_raw_item_id=p.raw_id)
            key=str(uuid4());a=r.service.store.create_lecture(payload,key);b=r.service.store.create_lecture(payload,key)
            assert a['id']==b['id']==p.lecture['id']
            with pytest.raises(PersonalError):r.service.store.create_lecture(payload.model_copy(update={'title':'changed'}),key)
            with pytest.raises(PersonalError) as missing_course:
                r.service.store.create_lecture(LectureInput(title='x',course_id='course-other',notes_raw_item_id=p.raw_id),str(uuid4()))
            assert missing_course.value.code=='invalid_course'
            with pytest.raises(ValidationError):LectureInput(title='x',lecture_date='20261026',notes_raw_item_id=p.raw_id)
            with r.store.connection() as c:assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==1
    asyncio.run(run())


@pytest.mark.parametrize('query,page',QUERIES)
def test_retrieval_twenty_fixture_questions(query,page,tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            result=r.service.retrieval.search(query,lecture_id=p.lecture['id'],limit=3)
            assert page in [b['page_index'] for b in result['items']]
            assert all(b['document_id']==r.service.store.book()['document_id'] for b in result['items'])
            assert len({b['content_hash'] for b in result['items']})==len(result['items'])
            for b in result['items']:
                source=r.service.contexts.block_source(b,'textbook')
                assert source['page']==b['page_index'] and f"第 {b['page_index']} 页" in source['caption']
    asyncio.run(run())


def test_retrieval_scope_dedupe_empty_and_multi_section(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            doc=r.service.store.book()['document_id']
            r.service.learning.store_page(doc,2,{'extracted_text':BOOK_PAGES[0],'metadata':{}},method='pypdf')
            found=r.service.retrieval.search('squeeze theorem',lecture_id=p.lecture['id'])
            assert len([b for b in found['items'] if b['resolved_text']==BOOK_PAGES[0]])==1
            assert not r.service.retrieval.search('zoological zebra',lecture_id=p.lecture['id'])['items']
            section=r.service.store.add_section(SectionInput(title='Squeeze only',start_page=1,end_page=1))
            s2=r.service.store.add_section(SectionInput(title='Sine',start_page=2,end_page=2))
            lecture=r.service.store.link_sections(p.lecture['id'],p.lecture['version'],[section['id'],s2['id']])
            assert len(r.service.retrieval.sections(lecture['id']))==2
            assert not r.service.retrieval.search('Euler constant',lecture_id=lecture['id'])['items']
            with pytest.raises(PersonalError):r.service.store.link_sections(lecture['id'],lecture['version'],['unknown'])
    asyncio.run(run())


def test_context_finite_source_ids_export_originals_and_no_personal_paths(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path,book=False)
            pack=context_pack(r.service,p.lecture['id'])
            with ZipFile(io.BytesIO(pack)) as z:
                manifest=json.loads(z.read('manifest.json'))
                text=z.read('context.md').decode()
                assert manifest['missing'] and '2026-10-26' in text and '9787301349656' in text
                assert len(manifest['sources'])<=14 and sum(len(s['text']) for s in manifest['sources'])<=18000
                assert all(s['document_id'] and s['page'] and s['block_id'] for s in manifest['sources'])
                assert any(n.startswith('pages/') for n in z.namelist())
                assert str(tmp_path) not in text and 'test-key' not in text
            assert not r.provider.calls
    asyncio.run(run())


@pytest.mark.parametrize('mutation,code',[
    ('teacher','teacher_claim'),('fake_page','model_page_label'),('history','invented_history'),
    ('source','unknown_source'),('quote','invalid_quote'),('notes_role','notes_only'),('schema','review_schema'),('low_ocr','uncertain_formula')])
def test_review_rejects_unsupported_claims_and_bad_schema(mutation,code,tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            context=r.service.contexts.build(p.lecture['id']);output=review_output(context)
            if mutation=='teacher':output['textbook_supplement'][0]['text']='老师漏讲了夹逼准则'
            elif mutation=='fake_page':output['must_master'][0]['text']='教材第 999 页说明了这个定义'
            elif mutation=='history':output['mistakes']['message']='你经常忘记极限的条件'
            elif mutation=='source':output['must_master'][0]['citations'][0]['source_id']='fake-source'
            elif mutation=='quote':output['must_master'][0]['citations'][0]['quote']='fictional quote'
            elif mutation=='notes_role':output['notes_taught']=[output['must_master'][0]]
            elif mutation=='schema':output['self_tests']=[]
            elif mutation=='low_ocr':
                for s in context['sources']:
                    if s['source_kind']=='notes':s['quality']='ocr_unverified'
            with pytest.raises(ExtractionFailure) as error:validate_review(encode(output),context)
            assert error.value.code==code
    asyncio.run(run())


def test_review_versions_adoption_cache_and_proposal_batch_no_auto_task(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            first=await r.service.generate(p.lecture['id'],generate_input(p.lecture));await finish(r.service)
            job=r.service.jobs.get(first['job']['id']);assert job['status']=='completed',job
            a=r.service.artifacts.get(job['result']['artifact_id'])
            assert a['status']=='needs_review' and len(a['content']['self_tests'])==3
            assert a['cost']['text_calls']==2 and a['cost']['images']==0
            old=encode(a['content']);ctx=r.service.contexts.build(p.lecture['id'])
            r.service.artifacts.feedback(a['id'],FeedbackInput(action='accept',expected_lecture_version=p.lecture['version']))
            adopted=r.service.store.lecture(p.lecture['id'])
            cached=await r.service.generate(p.lecture['id'],generate_input(adopted))
            assert cached['reused'] and len(r.provider.calls)==2
            assert not r.service.artifacts.get(a['id'])['lecture_version_changed']
            second=await r.service.generate(p.lecture['id'],generate_input(adopted,force=True));await finish(r.service)
            b=r.service.artifacts.get(r.service.jobs.get(second['job']['id'])['result']['artifact_id'])
            assert b['version']==2 and encode(r.service.artifacts.get(a['id'])['content'])==old
            r.service.artifacts.feedback(b['id'],FeedbackInput(action='accept',expected_lecture_version=adopted['version']))
            assert r.service.artifacts.get(a['id'])['status']=='superseded'
            current=r.service.store.lecture(p.lecture['id'])
            staged=stage_proposals(r.service,b['id'],current['version'])
            again=stage_proposals(r.service,a['id'],r.service.store.lecture(p.lecture['id'])['version'])
            assert again['snapshot_id']==staged['snapshot_id'] and again['reused']
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM task').fetchone()[0]==0
                assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0
            bundle=ReviewStore(r.store).get(staged['snapshot_id'])
            assert bundle['draft']['events'][0]['actions'][0]['kind']=='suggested' and not bundle['extraction']
            draft=copy.deepcopy(bundle['draft']);draft['events'][0]['participation']='yes'
            payload=ConfirmationInput(expected_version=bundle['version'],events=draft['events'],acknowledged_warnings=True)
            confirmed=ReviewStore(r.store).confirm(staged['snapshot_id'],payload)
            assert len(confirmed['events'][0]['tasks'])==1
    asyncio.run(run())


def test_vision_uncertain_cached_pages_and_explicit_retry_no_blind_replay(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path,notes_quality='ocr_unverified')
            image=tmp_path/'page.png';Image.new('RGB',(200,200),'white').save(image)
            monkeypatch.setattr(r.service.learning,'image_path',lambda *a:image)
            r.provider.clarity='uncertain'
            first=await r.service.generate(p.lecture['id'],generate_input(p.lecture));await finish(r.service)
            assert r.provider.calls==[('vision',1),'evidence','composition']
            job=r.service.jobs.get(first['job']['id']);assert job['status']=='completed',job
            assert r.service.learning.page(p.note['id'],1)['vision_status']=='uncertain'
            artifact=r.service.artifacts.get(job['result']['artifact_id'])
            assert artifact['content']['notes_taught'][0]['provenance']=='uncertain' and artifact['status']=='needs_review'
            assert artifact['cost']['image_calls']==1 and artifact['cost']['images']==1
            cached=await r.service.generate(p.lecture['id'],generate_input(p.lecture));await finish(r.service)
            assert cached['reused'] and len(r.provider.calls)==3
            vision=await r.service.start_vision(p.lecture['id'],VisionInput(request_id=str(uuid4()),document_id=p.note['id'],pages=[1],confirm_cost=True,force=False));await finish(r.service)
            assert len(r.provider.calls)==3 and r.service.jobs.get(vision['id'])['status']=='completed'
            r.provider.clarity='clear'
            explicit=await r.service.start_vision(p.lecture['id'],VisionInput(request_id=str(uuid4()),document_id=p.note['id'],pages=[1],confirm_cost=True,force=True));await finish(r.service)
            assert len(r.provider.calls)==4 and r.service.learning.page(p.note['id'],1)['vision_status']=='clear'
            assert len(r.service.learning.blocks([p.note['id']],current=False))>=4
    asyncio.run(run())


def test_unknown_ai_outcome_and_restart_preserve_sources_no_automatic_retry(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            r.provider.failure=ExtractionFailure('timeout','测试超时，结果未知。')
            payload=generate_input(p.lecture)
            first=await r.service.generate(p.lecture['id'],payload);await finish(r.service)
            same=await r.service.generate(p.lecture['id'],payload);await finish(r.service)
            assert same['job']['id']==first['job']['id'] and len(r.provider.calls)==1
            assert r.service.cost(first['job']['id'])['estimated_cost_cny'] is None
            with r.store.connection(write=True) as c:
                c.execute("UPDATE attachment_job SET status='running' WHERE id=?",(first['job']['id'],))
                c.execute("UPDATE school_ai_attempt SET status='running' WHERE job_id=?",(first['job']['id'],))
            r.service.jobs.recover()
            assert r.service.jobs.get(first['job']['id'])['status']=='recovery_needed'
            assert len(r.provider.calls)==1 and r.docs.files.path(r.docs.files.get(p.note['attachment_id'])).is_file()
            assert r.service.artifacts.get(r.service.jobs.get(first['job']['id'])['result']['artifact_id'])['status']=='recovery_needed'
    asyncio.run(run())


def test_local_partial_index_resume_skips_successful_pages(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            doc=r.service.store.book()['document_id']
            # A new extraction process version is not automatic: explicitly reset only this fixture's two page pointers.
            with r.store.connection(write=True) as c:
                c.execute("UPDATE document_page SET current_extraction_key=NULL,status='pending' WHERE document_id=? AND page_index IN (2,3)",(doc,))
            real=r.docs.worker;failed=[False]
            def worker(path,op,dest,timeout):
                if op=='school-page:3' and not failed[0]:failed[0]=True;raise PersonalError('测试页提取失败','fixture_failure',409)
                return real(path,op,dest,timeout)
            monkeypatch.setattr(r.docs,'worker',worker)
            job=r.service.start_index(p.sections[0]['id'],str(uuid4()));await finish(r.service)
            first=r.service.jobs.get(job['id']);assert first['status']=='partial' and first['result']['cached']==3
            block_ids=[b['id'] for b in r.service.learning.blocks([doc]) if b['page_index']==2]
            again=r.service.start_index(p.sections[0]['id'],str(uuid4()),retry=True);await finish(r.service)
            assert again['id']==job['id'] and r.service.jobs.get(job['id'])['status']=='completed'
            assert r.service.jobs.get(job['id'])['result']['cached']==4
            assert [b['id'] for b in r.service.learning.blocks([doc]) if b['page_index']==2]==block_ids
    asyncio.run(run())


def test_desktop_api_denies_lan_and_external_origin_and_fixture_formal_gate(tmp_path):
    async def run():
        async with rig(tmp_path,fixture_mode=False) as r:
            p=prepared(r.service,tmp_path)
            app=FastAPI();app.state.school_service=r.service;app.include_router(router)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1)),base_url='http://127.0.0.1:8765') as client:
                assert (await client.get('/api/personal/school')).status_code==200
                assert (await client.post('/api/personal/school/lectures',json={},headers={'Origin':'https://evil.test'})).status_code==403
                assert (await client.get('/api/personal/school',headers={'Origin':'http://localhost:9999'})).status_code==403
                response=await client.post(f"/api/personal/school/lectures/{p.lecture['id']}/generate",json=generate_input(p.lecture).model_dump())
                assert response.status_code==409 and response.json()['detail']['code']=='fixture_not_formal'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('192.168.1.3',1)),base_url='http://127.0.0.1:8765') as client:
                assert (await client.get('/api/personal/school')).status_code==403
            assert not r.provider.calls
    asyncio.run(run())


def test_store_first_textbook_intake_reuses_original_no_whole_book_parse(tmp_path):
    from server.test_collector import rig as collector_rig, payload
    from server.documents.api import router as document_router, UploadBodyLimit
    async def run():
        async with collector_rig(tmp_path) as c:
            service=SchoolService(c.store,c.docs,c.extraction,fixture_mode=True)
            app=FastAPI();app.state.school_service=service;app.state.document_service=c.docs
            app.state.inbox_service=c.service;app.include_router(router);app.include_router(document_router);app.add_middleware(UploadBodyLimit)
            path=text_pdf(tmp_path/'fixture-book.pdf',BOOK_PAGES)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1)),base_url='http://127.0.0.1:8765') as client:
                key=str(uuid4());files={'files':('fixture-book.pdf',path.read_bytes(),'application/pdf')}
                response=await client.post('/api/personal/collect-files?inbox=1&purpose=textbook',files=files,headers={'Idempotency-Key':key})
                assert response.status_code==201,response.text
                receipt=response.json();assert receipt['saved']
                await asyncio.gather(*list(c.service.tasks.values()))
                with c.store.connection() as db:
                    assert db.execute('SELECT count(*) FROM attachment_job').fetchone()[0]==0
                    assert db.execute('SELECT kind FROM raw_item WHERE id=?',(receipt['submission_id'],)).fetchone()[0]=='inbox_textbook'
                response=await client.put('/api/personal/school/textbook/file',json={'raw_item_id':receipt['submission_id'],'expected_version':1,'edition_confirmed':True})
                assert response.status_code==200,response.text
                await finish(service)
                book=(await client.get('/api/personal/school/textbook')).json()
                assert book['document']['page_count']==5
                assert not service.learning.blocks([book['document_id']])
                duplicate=await client.post('/api/personal/collect-files?inbox=1&purpose=textbook',files=files,headers={'Idempotency-Key':str(uuid4())})
                assert duplicate.json()['submission_id']==receipt['submission_id']
                for purpose,limit in [('',60_000_000),('?inbox=1&purpose=textbook',251_000_000)]:
                    oversize=await client.post('/api/personal/collect-files'+purpose,content=b'x',headers={'content-length':str(limit+1)})
                    assert oversize.status_code==413
                assert (await client.post('/api/personal/collect-files?inbox=1&purpose=textbook',files=files,headers={'Origin':'https://evil.test'})).status_code==403
            await service.close()
    asyncio.run(run())


def test_document_corrupt_and_ocr_failure_keep_original(tmp_path,monkeypatch):
    from server.documents.policy import DocumentError
    async def run():
        async with rig(tmp_path) as r:
            broken=tmp_path/'broken.pdf';broken.write_bytes(b'%PDF-1.7\ninvalid')
            raw=r.docs.files.collect([(broken.name,'application/pdf',broken)],'','broken',str(uuid4()))
            doc=r.service.learning.register(raw['snapshot']['attachments'][0]['id'])
            job=r.service.start_catalog(doc['id'],str(uuid4()));await finish(r.service)
            assert r.service.jobs.get(job['id'])['status']=='failed'
            assert r.docs.files.path(r.docs.files.get(doc['attachment_id'])).read_bytes()==broken.read_bytes()
            blank=tmp_path/'blank.pdf';w=PdfWriter();w.add_blank_page(width=300,height=400);w.write(blank)
            monkeypatch.setattr('server.school.pdf_pages.native_ocr',lambda *a: ([],{'quality':'low'}))
            with pytest.raises(DocumentError):parse_page(blank,1,tmp_path)
            assert blank.exists()
    asyncio.run(run())


def test_response_lost_after_vision_does_not_replay_original_request(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path,notes_quality='ocr_unverified')
            image=tmp_path/'page.png';Image.new('RGB',(20,20),'white').save(image)
            monkeypatch.setattr(r.service.learning,'image_path',lambda *a:image)
            payload=generate_input(p.lecture)
            first=await r.service.generate(p.lecture['id'],payload);await finish(r.service)
            second=await r.service.generate(p.lecture['id'],payload);await finish(r.service)
            assert first['job']['id']==second['job']['id'] and r.provider.calls==[('vision',1),'evidence','composition']
            with pytest.raises(PersonalError):
                await r.service.generate(p.lecture['id'],payload.model_copy(update={'force':True}))
    asyncio.run(run())


def test_ai_preflight_failure_has_zero_issued_calls_and_no_retry(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            async def preflight(*args):raise ExtractionFailure('invalid_endpoint','未发出请求。',attempts=0)
            r.provider.extract=preflight
            job=await r.service.generate(p.lecture['id'],generate_input(p.lecture));await finish(r.service)
            cost=r.service.cost(job['job']['id'])
            assert cost['text_calls']==0 and cost['estimated_cost_cny']==0
            assert r.service.jobs.get(job['job']['id'])['status']=='failed'
    asyncio.run(run())


def test_adopted_review_survives_failed_revision_and_changed_section_is_visible(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            first=await r.service.generate(p.lecture['id'],generate_input(p.lecture));await finish(r.service)
            aid=r.service.jobs.get(first['job']['id'])['result']['artifact_id']
            r.service.artifacts.feedback(aid,FeedbackInput(action='accept',expected_lecture_version=p.lecture['version']))
            lecture=r.service.store.lecture(p.lecture['id'])
            r.provider.failure=ExtractionFailure('timeout','测试超时。')
            await r.service.generate(lecture['id'],generate_input(lecture,force=True));await finish(r.service)
            assert r.service.store.lecture(lecture['id'])['status']=='accepted'
            assert r.service.artifacts.get(aid)['adopted'] and not r.service.artifacts.get(aid)['lecture_version_changed']
            section=r.service.store.add_section(SectionInput(title='Different current scope',start_page=1,end_page=1))
            r.service.store.link_sections(lecture['id'],lecture['version'],[section['id']])
            assert r.service.artifacts.get(aid)['lecture_version_changed']
    asyncio.run(run())


def test_unreadable_book_page_can_be_located_and_explicitly_reviewed(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            doc=r.service.store.book()['document_id']
            with r.store.connection(write=True) as c:
                c.execute("UPDATE document_page SET current_extraction_key=NULL,status='failed' WHERE document_id=? AND page_index=2",(doc,))
            image=tmp_path/'scan.png';Image.new('RGB',(20,20),'white').save(image)
            monkeypatch.setattr(r.service.learning,'image_path',lambda *a:image)
            app=FastAPI();app.state.school_service=r.service;app.include_router(router)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1)),base_url='http://127.0.0.1:8765') as client:
                response=await client.get(f"/api/personal/school/lectures/{p.lecture['id']}/page-source",params={'document_id':doc,'page':2})
                source=response.json();assert response.status_code==200,response.text
                assert source['kind']=='page_image' and source['page']==2 and source['source_kind']=='textbook'
                assert '高等数学（第二版）（上）' in source['caption'] and 'PDF 第 2 页' in source['caption']
                assert (await client.get(f"/api/personal/school/lectures/{p.lecture['id']}/page-source",params={'document_id':doc,'page':6})).status_code==400
                result=await client.post(f"/api/personal/school/lectures/{p.lecture['id']}/vision",json={'request_id':str(uuid4()),'document_id':doc,'pages':[2],'confirm_cost':True})
                assert result.status_code==202,result.text
                await finish(r.service)
                assert r.provider.calls==[('vision',1)]
                assert r.service.learning.page(doc,2)['vision_status']=='clear'
    asyncio.run(run())


def test_real_provider_keeps_audit_metadata_local_and_preserves_quotable_evidence():
    from server.school.provider import SchoolProvider
    class CaptureTransport:
        async def request(self,body,config,key,**kwargs):
            self.body=body
            return 'captured_without_network'
    transport=CaptureTransport();provider=SchoolProvider(transport)
    source={'source_id':'src-real','source_kind':'notes','kind':'text','text':'原页末行写“=1”。','quality':'vision_uncertain',
            'uncertainties':['LOCAL-AUDIT-ONLY'],'caption':'LOCAL-CAPTION','original_url':'/local-original','content_hash':'LOCAL-HASH'}
    context={'context_id':'ctx','lecture':{'lecture_date':'2026-10-26'},'sources':[source],
             'mistakes':{'available':False,'items':[]},'budget':{'max_sources':14}}
    config=SimpleNamespace(base_url='https://api.deepseek.com',model='deepseek-flash',max_output_tokens=8192)
    assert asyncio.run(provider.extract(context,config,'test-key',lambda:None))=='captured_without_network'
    sent=json.loads(transport.body['messages'][1]['content'])
    assert sent['facts'][0]['source_id']==source['source_id']
    assert sent['facts'][0]['text']==source['text'] and sent['facts'][0]['uncertain']
    assert 'LOCAL-AUDIT-ONLY' not in json.dumps(sent) and 'LOCAL-HASH' not in json.dumps(sent)
    assert context['sources'][0]['uncertainties']==['LOCAL-AUDIT-ONLY']
