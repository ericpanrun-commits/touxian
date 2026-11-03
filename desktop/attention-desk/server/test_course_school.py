"""Course-scoped classrooms and evidence, with no real provider requests."""
import asyncio
import io
from uuid import uuid4
from zipfile import ZipFile

import pytest

from server.courses import CourseStore
from server.inbox_store import InboxStore
from server.personal_store import PersonalError
from server.school.export import context_pack
from server.school.models import COURSE,TEXTBOOK,UNIT,LectureInput,SectionInput,BookMetadataInput
from server.school.pdf_pages import catalog
from server.test_documents import text_pdf
from server.test_school import rig,prepared,generate_input


def intake(service,root,title,text,course):
    file=text_pdf(root/(title+'.pdf'),[text])
    saved=service.documents.files.collect([(file.name,'application/pdf',file)],'',title,str(uuid4()))
    raw=saved['snapshot']['raw_item_id'];InboxStore(service.personal).link(raw,1,course,'')
    doc=service.learning.register(saved['snapshot']['attachments'][0]['id'])
    service.learning.catalog(doc['id'],catalog(file))
    service.learning.store_page(doc['id'],1,{'extracted_text':text,'metadata':{}},method='pypdf',quality='text_extracted')
    return raw,service.learning.get(doc['id'])


def english_course(service):return CourseStore(service.personal).create('隔离测试 · 大学英语')['id']


def test_nonmath_class_without_a_book_preserves_notes_and_exports_its_real_scope(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            raw,doc=intake(r.service,tmp_path,'english-notes','Synthetic conditional sentences from classroom notes.',course)
            payload=LectureInput(course_id=course,title='条件句',lecture_date='2026-10-28',notes_raw_item_id=raw)
            assert payload.learning_unit_id is None
            lecture=r.service.store.create_lecture(payload,str(uuid4()))
            assert lecture['course']['id']==course and lecture['textbook'] is None
            assert r.service.store.overview(course_id=course)['total']==1
            assert r.service.store.overview()['total']==0
            assert r.service.store.note_choices(course)[0]['raw_item_id']==raw
            frame=r.service.contexts.build(lecture['id'],allow_incomplete=True)
            assert frame['input_versions']['textbook'] is None and frame['missing']
            assert frame['course']['id']==course and all(s['source_kind']=='notes' for s in frame['sources'])
            assert r.service.quote_cost(lecture['id'])['context_missing']
            assert r.service.contexts.build(lecture['id'])['source_scope']=='notes_only'
            assert r.provider.calls==[]
            with ZipFile(io.BytesIO(context_pack(r.service,lecture['id']))) as archive:
                markdown=archive.read('context.md').decode()
                assert '隔离测试 · 大学英语' in markdown and '课程：高等数学' not in markdown
    asyncio.run(run())


def test_new_course_class_creation_checks_course_unit_and_request_identity(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            math=prepared(r.service,tmp_path,book=False);course=english_course(r.service)
            raw,doc=intake(r.service,tmp_path,'other-notes','Synthetic source in the second course.',course)
            payload=LectureInput(course_id=course,title=math.lecture['title'],lecture_date='2026-10-26',notes_raw_item_id=raw)
            key=str(uuid4());created=r.service.store.create_lecture(payload,key,automatic_note=True)
            assert created['id']!=math.lecture['id'] and r.service.store.create_lecture(payload,key)['id']==created['id']
            with pytest.raises(PersonalError):
                r.service.store.create_lecture(payload.model_copy(update={'learning_unit_id':UNIT}),str(uuid4()))
            with pytest.raises(PersonalError):
                r.service.store.create_lecture(payload.model_copy(update={'notes_raw_item_id':math.raw_id}),str(uuid4()))
            with pytest.raises(PersonalError):
                r.service.store.confirm_note_lecture(raw,math.lecture['id'])
            with pytest.raises(PersonalError):
                r.service.store.create_lecture(payload.model_copy(update={'title':'Changed'}),key)
            assert r.service.store.overview(course_id=course)['total']==1
    asyncio.run(run())


def test_book_registry_is_scoped_idempotent_and_does_not_choose_math_defaults(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service);input=BookMetadataInput(course_id=course,title=' Synthetic grammar ',edition=' first ')
            key=str(uuid4());book=r.service.store.register_book(input,key)
            assert book['id']!=TEXTBOOK and book['course_id']==course and book['document'] is None and book['role']=='primary'
            assert r.service.store.register_book(input,key)['id']==book['id']
            assert r.service.store.register_book(input,str(uuid4()))['id']==book['id']
            assert r.service.store.book()['id']==TEXTBOOK
            assert r.service.store.book(course)['id']==book['id']
            with pytest.raises(PersonalError):r.service.store.register_book(input.model_copy(update={'title':'Changed'}),key)
            with pytest.raises(PersonalError):r.service.store.register_book(input.model_copy(update={'course_id':'missing'}),str(uuid4()))
            companion=r.service.store.register_book(input.model_copy(update={'title':'Synthetic workbook'}),str(uuid4()))
            assert companion['role']=='companion' and r.service.store.book(course)['companions'][0]['id']==companion['id']
            assert len(r.service.store.books(course))==2
    asyncio.run(run())


def test_shared_original_books_have_distinct_course_sections_and_retrieval_never_widens(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            raw,note=intake(r.service,tmp_path,'english-class','Synthetic conditional classroom notes.',course)
            lecture=r.service.store.create_lecture(LectureInput(course_id=course,title='Conditional sentence',notes_raw_item_id=raw),str(uuid4()))
            book=r.service.store.register_book(BookMetadataInput(course_id=course,title='Synthetic English grammar'),str(uuid4()))
            text='Synthetic conditional grammar requires an explicit premise. This text is isolated test evidence.'
            english_raw,shared=intake(r.service,tmp_path,'english-book',text,course)
            r.service.store.link_book(english_raw,book['version'],'chapter_scan',fixture=True,textbook_id=book['id'])
            english_section=r.service.store.add_section(SectionInput(textbook_id=book['id'],title='Conditional grammar',start_page=1,end_page=1))
            assert english_section['learning_unit_id'] is None
            math_raw,same=intake(r.service,tmp_path,'math-copy',text,COURSE)
            assert same['id']==shared['id']
            r.service.store.link_book(math_raw,1,'chapter_scan',fixture=True)
            math_section=r.service.store.add_section(SectionInput(title='Conditional grammar',start_page=1,end_page=1))
            assert math_section['id']!=english_section['id']
            lecture=r.service.store.link_sections(lecture['id'],lecture['version'],[english_section['id']])
            with pytest.raises(PersonalError):r.service.store.link_sections(lecture['id'],lecture['version'],[math_section['id']])
            assert [s['id'] for s in r.service.retrieval.sections(lecture['id'])]==[english_section['id']]
            result=r.service.retrieval.search('conditional grammar',lecture_id=lecture['id'])
            assert result['items'] and result['scope']['course_id']==course
            assert all(item['section_id']==english_section['id'] for item in result['items'])
            assert not r.service.retrieval.search('conditional',section_ids=[english_section['id']])['items']
            assert not r.service.retrieval.search('conditional',section_ids=[math_section['id']],course_id=course)['items']
            with pytest.raises(PersonalError):r.service.retrieval.search('conditional',lecture_id=lecture['id'],course_id=COURSE)
            context=r.service.contexts.build(lecture['id'])
            assert context['input_versions']['textbook']['id']==book['id']
            assert all(source['caption'].startswith(book['title']) for source in context['sources'] if source['source_kind']=='textbook')
            assert all(item['section']['textbook_id']==book['id'] for item in r.service.retrieval.suggest(lecture))
            assert english_raw not in {item['raw_item_id'] for item in r.service.store.note_choices(course)}
    asyncio.run(run())


def test_unrelated_primary_book_does_not_replace_a_class_selected_source(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            raw,note=intake(r.service,tmp_path,'class-note','Synthetic conditional sentence notes.',course)
            lecture=r.service.store.create_lecture(LectureInput(course_id=course,title='Conditional sentence',notes_raw_item_id=raw),str(uuid4()))
            chosen=r.service.store.register_book(BookMetadataInput(course_id=course,title='Selected source',role='companion'),str(uuid4()))
            source,doc=intake(r.service,tmp_path,'source-book','Synthetic conditional sentence textbook evidence.',course)
            r.service.store.link_book(source,chosen['version'],'chapter_scan',fixture=True,textbook_id=chosen['id'])
            section=r.service.store.add_section(SectionInput(textbook_id=chosen['id'],title='Conditional sentence',start_page=1,end_page=1))
            lecture=r.service.store.link_sections(lecture['id'],lecture['version'],[section['id']])
            before=r.service.contexts.build(lecture['id'])
            new=r.service.store.register_book(BookMetadataInput(course_id=course,title='Later unrelated primary'),str(uuid4()))
            assert r.service.store.book(course)['id']==new['id']
            after=r.service.contexts.build(lecture['id'])
            assert after['context_id']==before['context_id']
            assert after['input_versions']==before['input_versions']
            assert after['input_versions']['textbook']['id']==chosen['id']
    asyncio.run(run())


def test_no_book_class_does_not_stop_the_read_only_cloud_projection(tmp_path):
    from server.cloud_sync.projection import CloudProjection
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            raw,doc=intake(r.service,tmp_path,'cloud-class','Synthetic English classroom source.',course)
            lecture=r.service.store.create_lecture(LectureInput(course_id=course,title='English classroom',notes_raw_item_id=raw),str(uuid4()))
            records=CloudProjection(r.store,tmp_path/'projection').records()
            detail=next(item['payload'] for item in records if item['kind']=='lecture' and item['id']==lecture['id'])
            assert detail['course_id']==course and detail['textbook'] is None
            frame=next(item['payload'] for item in records if item['id']=='/school/lectures/'+lecture['id']+'/context')
            assert frame['course']['id']==course and frame['input_versions']['textbook'] is None
            quote=next(item['payload'] for item in records if item['id']=='/school/lectures/'+lecture['id']+'/cost')
            assert quote['context_missing'] and r.provider.calls==[]
    asyncio.run(run())


def test_book_replacement_invalidates_current_acceptance_without_erasing_prior_audit(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            book=r.service.store.register_book(BookMetadataInput(course_id=course,title='Synthetic book'),str(uuid4()))
            raw,doc=intake(r.service,tmp_path,'replacement','Synthetic replacement source.',course)
            with r.store.connection(write=True) as c:
                c.execute("UPDATE textbook SET acceptance_status='accepted',acceptance_json='{}',accepted_at='2026-10-01' WHERE id=?",(book['id'],))
            updated=r.service.store.link_book(raw,book['version'],'full_textbook',textbook_id=book['id'])
            assert updated['acceptance_status']=='pending_evaluation'
            assert updated['acceptance_json']=='{}' and updated['accepted_at']=='2026-10-01'
    asyncio.run(run())


def test_note_course_changed_during_registration_cannot_commit_a_stale_class(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            raw,doc=intake(r.service,tmp_path,'racing-note','Synthetic source retained across a course edit.',course)
            original=r.service.learning.register
            def changed(attachment):
                result=original(attachment)
                with r.store.connection(write=True) as c:c.execute('UPDATE raw_item SET inbox_course_id=? WHERE id=?',(COURSE,raw))
                return result
            monkeypatch.setattr(r.service.learning,'register',changed)
            with pytest.raises(PersonalError) as stale:
                r.service.store.create_lecture(LectureInput(course_id=course,title='Stale class',notes_raw_item_id=raw),str(uuid4()))
            assert stale.value.code=='lecture_context_mismatch'
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM lecture').fetchone()[0]==0
                assert c.execute('SELECT inbox_course_id FROM raw_item WHERE id=?',(raw,)).fetchone()[0]==COURSE
    asyncio.run(run())


def test_local_course_api_selects_exact_book_and_keeps_existing_access_boundary(tmp_path):
    import httpx
    from fastapi import FastAPI
    from server.school.api import router
    from server.test_school import finish
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            raw,note=intake(r.service,tmp_path,'api-notes','Synthetic English classroom note.',course)
            book_raw,doc=intake(r.service,tmp_path,'api-book','Synthetic conditional grammar from the chosen book.',course)
            app=FastAPI();app.state.school_service=r.service;app.include_router(router)
            transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1))
            async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1:8765') as client:
                path='/api/personal/school'
                overview=(await client.get(path,params={'course_id':course})).json()
                assert overview['course']['id']==course and overview['textbook'] is None
                assert (await client.get(path+'/textbook',params={'course_id':course})).json() is None
                body={'course_id':course,'title':'Synthetic API grammar'};headers={'Idempotency-Key':str(uuid4())}
                assert (await client.post(path+'/textbooks',json=body,headers={**headers,'Origin':'https://untrusted.example'})).status_code==403
                registered=await client.post(path+'/textbooks',json=body,headers=headers);assert registered.status_code==201
                book=registered.json();assert book['course_id']==course
                assert (await client.post(path+'/textbooks',json=body,headers=headers)).json()['id']==book['id']
                response=await client.put(path+'/textbook/file',json={'textbook_id':book['id'],'raw_item_id':book_raw,
                    'expected_version':book['version'],'file_role':'full_textbook','edition_confirmed':True})
                assert response.status_code==200;linked=response.json()['textbook'];assert linked['document_id']==doc['id']
                await finish(r.service)
                section={'textbook_id':book['id'],'title':'Conditional grammar','start_page':1,'end_page':1,'expected_version':linked['version']}
                assert (await client.post(path+'/sections',json={**section,'expected_version':1})).status_code==409
                saved=await client.post(path+'/sections',json=section);assert saved.status_code==201
                created=await client.post(path+'/lectures',json={'course_id':course,'title':'Conditional grammar','notes_raw_item_id':raw},
                    headers={'Idempotency-Key':str(uuid4())})
                assert created.status_code==201;lecture=created.json();assert lecture['course_id']==course
                assert (await client.put(path+'/lectures/'+lecture['id']+'/sections',json={'expected_version':lecture['version'],
                    'section_ids':[saved.json()['id']]})).status_code==200
                result=(await client.get(path+'/lectures/'+lecture['id']+'/retrieve',params={'q':'conditional grammar'})).json()
                assert result['items'] and all(item['caption'].startswith(book['title']) for item in result['items'])
                assert (await client.get(path+'/textbooks',params={'course_id':course})).json()['items'][0]['id']==book['id']
                assert (await client.get(path+'/textbook')).json()['id']==TEXTBOOK
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('192.168.1.9',1)),base_url='http://127.0.0.1:8765') as client:
                assert (await client.get('/api/personal/school/textbooks')).status_code==403
            assert r.provider.calls==[]
    asyncio.run(run())
