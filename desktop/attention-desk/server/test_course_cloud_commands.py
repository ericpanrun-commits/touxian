"""Controlled course/book commands retain the actual domain receipt boundary."""
import asyncio
import copy
from uuid import uuid4

import pytest

from server.cloud_sync.commands import CommandExecutor
from server.cloud_sync.projection import CloudProjection
from server.inbox_store import InboxStore
from server.personal_store import PersonalError
from server.school.models import BookMetadataInput
from server.test_course_school import english_course,intake
from server.test_school import rig,finish


def command(path,body,method='POST'):
    return {'id':str(uuid4()),'path':path,'method':method,'body':body}


def test_cloud_book_registration_section_and_projection_share_canonical_course_ids(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service);executor=CommandExecutor(r.store,r.service,r.extract,None)
            create=command('/school/textbooks',{'course_id':course,'title':'Isolated English grammar'})
            book=await executor.execute(create)
            assert await executor.execute(create)==book and book['course_id']==course and book['document_id'] is None
            raw,doc=intake(r.service,tmp_path,'grammar-file','An explicit condition is required before applying this example.',course)
            link=command('/school/textbook/file',{'textbook_id':book['id'],'raw_item_id':raw,'expected_version':book['version'],
                'file_role':'chapter_scan','edition_confirmed':True},'PUT')
            linked=await executor.execute(link);await finish(r.service)
            assert linked['textbook']['document_id']==doc['id']
            current=r.service.store.textbook(book['id'])
            add=command('/school/sections',{'textbook_id':book['id'],'expected_version':current['version'],
                'title':'Conditional grammar','start_page':1,'end_page':1})
            section=await executor.execute(add)
            assert await executor.execute(add)==section and section['learning_unit_id'] is None
            index=await executor.execute(command('/school/sections/'+section['id']+'/index',{'retry':False}))
            await finish(r.service);assert r.service.jobs.get(index['id'])['status']=='completed'
            records=CloudProjection(r.store,tmp_path/'cloud').records()
            published=next(row['payload'] for row in records if row['kind']=='textbook' and row['id']==book['id'])
            assert published['course_id']==course and published['sections'][0]['id']==section['id']
            scoped=next(row['payload'] for row in records if row['kind']=='facade' and row['id']=='/school?course_id='+course)
            assert scoped['course']['id']==course and scoped['textbook']['id']==book['id'] and scoped['lectures']==[]
            legacy=next(row['payload'] for row in records if row['kind']=='facade' and row['id']=='/school/textbook')
            assert legacy['id']=='textbook-huang-v2-upper' and legacy['course_id']=='course-math-ii'
            assert r.provider.calls==[]
    asyncio.run(run())


def test_lost_book_file_ack_replays_committed_outcome_without_reverting_a_later_file(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service);executor=CommandExecutor(r.store,r.service,r.extract,None)
            book=r.service.store.register_book(BookMetadataInput(course_id=course,title='Isolated book'),str(uuid4()))
            raw,doc=intake(r.service,tmp_path,'old-book','Original book file.',course)
            action=command('/school/textbook/file',{'textbook_id':book['id'],'raw_item_id':raw,'expected_version':1,
                'file_role':'full_textbook','edition_confirmed':True},'PUT')
            first=await executor.execute(action);await finish(r.service)
            replacement,new=intake(r.service,tmp_path,'new-book','Later explicitly selected book file.',course)
            current=r.service.store.link_book(replacement,first['textbook']['version'],'full_textbook',textbook_id=book['id'])
            def forbidden(*args):raise AssertionError('Committed command must not prepare or link files again')
            monkeypatch.setattr(r.service.store,'prepare_book_file',forbidden)
            replay=await executor.execute(action);await finish(r.service)
            assert replay['textbook']==first['textbook'] and replay['job']['id']==first['job']['id']
            assert r.service.store.textbook(book['id'])['document_id']==new['id']
            assert r.service.store.textbook(book['id'])['version']==current['version']
            changed=copy.deepcopy(action);changed['body']['raw_item_id']=replacement
            with pytest.raises(PersonalError) as conflict:await executor.execute(changed)
            assert conflict.value.code=='idempotency_conflict' and not r.provider.calls
    asyncio.run(run())


@pytest.mark.parametrize('change',['course','book_version','attachment'])
def test_prepared_book_original_is_rechecked_in_the_atomic_commit(tmp_path,change):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service)
            book=r.service.store.register_book(BookMetadataInput(course_id=course,title='Guarded book'),str(uuid4()))
            raw,doc=intake(r.service,tmp_path,'guarded-book','Original guarded book.',course)
            prepared=r.service.store.prepare_book_file(raw,book['id'])
            if change=='course':
                with r.store.connection(write=True) as c:c.execute("UPDATE raw_item SET inbox_course_id='course-math-ii' WHERE id=?",(raw,))
            elif change=='book_version':
                replacement,_=intake(r.service,tmp_path,'changed-book','New original selected before stale commit.',course)
                r.service.store.link_book(replacement,1,'chapter_scan',textbook_id=book['id'])
            else:prepared['attachment_id']=str(uuid4())
            with pytest.raises(PersonalError):
                r.service.store.link_registered_book(raw,1,'full_textbook',**prepared,textbook_id=book['id'])
            assert r.service.store.textbook(book['id'])['document_id']!=doc['id']
    asyncio.run(run())
