"""Failure injection at the existing intake and durable worker boundaries."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from server.cloud_sync.service import SyncError, retryable
from server.test_cloud_sync_worker import worker
from server.test_collector import rig, payload, count
from server.test_eric_inbox import settled


def test_learning_projection_failure_is_visible_and_recoverable(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            calls=[]
            async def ingest(raw_id, snapshot_id):
                calls.append((raw_id,snapshot_id))
                if len(calls)==1:raise sqlite3.OperationalError('private token must never reach logs')
            r.service.learning=SimpleNamespace(ingest=ingest)
            receipt=await r.service.submit('desktop','text',payload())
            failed=await settled(r,receipt)
            assert failed['processing_status']=='failed'
            assert failed['error']['code']=='learning_projection_failed'
            assert failed['original']['original_text']
            log=(tmp_path/'logs/mobile-collector.log').read_text(encoding='utf-8')
            entry=json.loads(log.splitlines()[-1])
            assert entry['job_id']==failed['id'] and entry['entity']=='raw_item'
            assert entry['status']=='failed' and entry['action']=='learning_projection'
            assert 'private token' not in log
            await r.service.retry(failed['id'],failed['version'])
            recovered=await settled(r,receipt)
            assert recovered['processing_status']=='needs_review'
            assert len(calls)==2 and count(r.store,'raw_item')==1
            assert count(r.store,'inbox_receipt')==1 and r.provider.calls==0
    asyncio.run(run())


@pytest.mark.parametrize('failure,expected',[
    (httpx.ConnectError('private token'),'pending'),
    (SyncError('rate_limited',429),'pending'),
    (SyncError('data_unavailable',503),'pending'),
    (SyncError('forbidden',403),'failed'),
    (SyncError('invalid_schema',400),'failed'),
    (SyncError('schema_mismatch',500),'failed'),
    (SyncError('source_file_changed'),'failed'),
    (sqlite3.IntegrityError('private token'),'failed'),
    (RuntimeError('private token'),'failed'),
])
def test_cloud_worker_never_acknowledges_failure_as_completed(tmp_path,failure,expected):
    async def run():
        acknowledgements=[]
        def handler(request):
            assert request.url.path.endswith('/worker/finish')
            acknowledgements.append(json.loads(request.content))
            return httpx.Response(200,json={})
        service,client=worker(tmp_path,handler)
        async def execute(_):raise failure
        async def publish():raise AssertionError('Failed work must not publish success')
        service.executor=SimpleNamespace(execute=execute);service.publish=publish
        command={'id':str(uuid4()),'lease_token':str(uuid4())}
        try:
            await service.process('commands',command)
            assert len(acknowledgements)==1
            ack=acknowledgements[0]
            assert ack['state']==expected and ack['id']==command['id']
            assert 'private token' not in json.dumps(ack)
        finally:await client.aclose()
    asyncio.run(run())


def test_worker_lease_loss_cancels_owner_without_publish_or_ack(tmp_path):
    async def run():
        service,client=worker(tmp_path,lambda _: (_ for _ in ()).throw(AssertionError('stale owner sent an ack')))
        entered=asyncio.Event();cancelled=asyncio.Event()
        async def execute(_):
            entered.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        async def renew(*_):
            await entered.wait()
            raise SyncError('stale_processing_lease',409)
        async def publish():raise AssertionError('stale owner published')
        service.executor=SimpleNamespace(execute=execute);service.renew=renew;service.publish=publish
        try:
            with pytest.raises(SyncError,match='stale_processing_lease'):
                await service.process('commands',{'id':str(uuid4()),'lease_token':str(uuid4())})
            assert cancelled.is_set()
        finally:await client.aclose()
    asyncio.run(run())


def test_sqlite_busy_is_temporary_but_schema_and_disk_errors_are_permanent(tmp_path):
    path=tmp_path/'locked.sqlite3'
    with sqlite3.connect(path) as holder,sqlite3.connect(path,timeout=0) as contender:
        holder.execute('CREATE TABLE item(id integer)')
        holder.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError) as busy:contender.execute('INSERT INTO item VALUES(1)')
        assert retryable(busy.value)
        holder.rollback()
        with pytest.raises(sqlite3.OperationalError) as schema:contender.execute('SELECT * FROM missing_table')
        assert not retryable(schema.value)
        assert not retryable(sqlite3.OperationalError('disk full'))


@pytest.mark.parametrize('content',[b'not json',b'{"detail":"gateway failed"}',b'[]',b'{"detail":{"code":{}}}'])
def test_malformed_gateway_errors_keep_their_transient_http_status(tmp_path,content):
    async def run():
        service,client=worker(tmp_path,lambda _:httpx.Response(503,content=content,headers={'Content-Type':'application/json'}))
        try:
            with pytest.raises(SyncError) as error:await service.request('POST','/worker/claim',{})
            assert error.value.status==503 and retryable(error.value)
        finally:await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('interrupted',[False,True])
def test_unexpected_learning_runner_failure_cannot_leave_running_artifact(tmp_path,interrupted):
    from server.learning.service import LearningService
    from server.learning.models import CapturedPage
    async def run():
        async with rig(tmp_path) as r:
            async def failing(*_):
                if interrupted:raise asyncio.CancelledError()
                raise RuntimeError('private provider token')
            service=LearningService(r.store,None,None,analysis_runner=failing)
            artifact=service.store.reserve_artifact('capture_analysis','synthetic-analysis')
            try:
                if interrupted:
                    with pytest.raises(asyncio.CancelledError):await service.call(artifact,CapturedPage,{})
                else:assert await service.call(artifact,CapturedPage,{}) is None
                with r.store.connection() as c:
                    row=c.execute('SELECT status,error_json,completed_at FROM learning_artifact WHERE id=?',(artifact['id'],)).fetchone()
                assert row['status']==('interrupted' if interrupted else 'needs_review')
                assert row['completed_at'] and 'private provider token' not in row['error_json']
            finally:await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('human_confirmed',[False,True])
def test_learning_background_error_is_visible_and_respects_later_confirmation(tmp_path,human_confirmed):
    from server.learning.service import LearningService
    from server.test_learning import capture
    async def run():
        async with rig(tmp_path) as r:
            service=LearningService(r.store,None,None)
            value=capture(service.store,'Synthetic learning source')
            if human_confirmed:service.store.capture_status(value['id'],'completed',parsed={'human':'confirmed'})
            async def failing():raise RuntimeError('private source token')
            try:
                service.schedule('capture:'+value['id'],failing)
                await asyncio.gather(*list(service.tasks.values()))
                result=service.capture_detail(value['id'])
                assert result['status']==('completed' if human_confirmed else 'needs_review')
                if human_confirmed:assert result['parsed']=={'human':'confirmed'}
                else:assert result['error']['code']=='background_failed'
                log=(tmp_path/'logs/mobile-collector.log').read_text(encoding='utf-8')
                assert value['id'] in log and 'private source token' not in log
            finally:await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('owner',['intake','learning'])
def test_status_write_failure_is_consumed_logged_and_never_becomes_completed(tmp_path,monkeypatch,owner):
    from contextlib import contextmanager
    from server.learning.service import LearningService
    from server.test_learning import capture
    async def run():
        async with rig(tmp_path) as r:
            service=LearningService(r.store,None,None) if owner=='learning' else r.service
            value=capture(service.store,'Synthetic retained source') if owner=='learning' else await r.service.submit('desktop','text',payload())
            identity=value['id'] if owner=='learning' else value['submission_id']
            original=r.store.connection;unhandled=[]
            loop=asyncio.get_running_loop();old_handler=loop.get_exception_handler()
            loop.set_exception_handler(lambda _,context:unhandled.append(context))
            @contextmanager
            def broken(*args,**kwargs):
                if kwargs.get('write'):raise sqlite3.OperationalError('private database error')
                with original(*args,**kwargs) as connection:yield connection
            try:
                with monkeypatch.context() as patch:
                    patch.setattr(r.store,'connection',broken)
                    if owner=='learning':
                        async def fail():raise RuntimeError('private work error')
                        service.schedule('capture:'+identity,fail)
                    async with asyncio.timeout(3):
                        while service.tasks:await asyncio.sleep(0)
                assert not unhandled
                log=(tmp_path/'logs/mobile-collector.log').read_text(encoding='utf-8')
                assert ('learning_status_write' if owner=='learning' else 'process_status_write') in log
                assert identity in log and 'private database error' not in log
                if owner=='learning':assert service.capture_detail(identity)['status']=='received'
                else:
                    pending=r.service.inbox.detail(identity)
                    assert pending['processing_status']!='completed'
                    await r.service.retry(identity,pending['version'])
                    assert (await settled(r,value))['processing_status']=='needs_review'
                    assert count(r.store,'raw_item')==1
            finally:
                loop.set_exception_handler(old_handler)
                if owner=='learning':await service.close()
    asyncio.run(run())
