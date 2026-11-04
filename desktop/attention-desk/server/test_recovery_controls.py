"""Local capture permission must not imply cloud upload or paid AI permission."""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from server.recovery_controls import controls
from server.cloud_sync.service import CloudSync, SyncError
from server.notes_anywhere.service import AnywhereNotes, NotesSyncError
from server.ai_transport import DeepSeekTransport
from server.extraction_errors import ExtractionFailure
from server.wechat_collect.config import WeChatConfig
from server.wechat_collect.scheduler import SchedulerPolicy


@pytest.fixture
def held(tmp_path, monkeypatch):
    path = tmp_path / 'recovery-controls.json'
    monkeypatch.setenv('ATTENTION_RECOVERY_CONTROL_PATH', str(path))
    def write(cloud=True, paid=True):
        path.write_text(json.dumps({'version': 1, 'hold_cloud_uploads': cloud, 'pause_paid_ai': paid}), encoding='utf-8')
    write()
    return path, write


def test_controls_are_independent_persistent_and_fail_closed(held):
    path, write = held
    write(cloud=False)
    assert controls()['pause_paid_ai'] and not controls()['hold_cloud_uploads']
    write(paid=False)
    assert controls()['hold_cloud_uploads'] and not controls()['pause_paid_ai']
    for invalid in ('{', '{"version":1}', 'null', '[]'):
        path.write_text(invalid)
        assert controls() == {'hold_cloud_uploads': True, 'pause_paid_ai': True, 'state': 'unreadable_held'}


def test_cloud_hold_only_reads_catalogues_and_never_resumes_other_work(tmp_path, held):
    async def run():
        calls=[]
        def handler(request):
            calls.append((request.method, request.url.path))
            assert request.method == 'GET' and request.content in (b'', b'null')
            return httpx.Response(200, json={'items': [], 'limit': 500})
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service=CloudSync(SimpleNamespace(path=tmp_path/'personal.sqlite3'),None,None,None,
            SimpleNamespace(read=lambda: {'enabled': True, 'worker_key': 'test-only'}),
            learning=SimpleNamespace(resume_pending_note_links=lambda: pytest.fail('No background mutations')),
            client=client)
        try:
            await service.sync_once()
            await service.sync_once()
            assert len(calls)==2
            assert service.state['uploads_held'] and service.state['phase']=='local_only'
            assert service.state.get('write_requests',0)==0
            assert service.workspace is None and service.heartbeat is None
            for method in ('POST','PUT','PATCH','DELETE'):
                with pytest.raises(SyncError,match='cloud_uploads_held'):
                    await service.request(method,'/worker/publish',{'records':['private']})
            for kwargs in ({'body': {'raw':'private'}}, {'content': b'private attachment'}):
                with pytest.raises(SyncError,match='cloud_uploads_held'):
                    await service.request('GET','/worker/resources',**kwargs)
            assert len(calls)==2
        finally:
            await client.aclose()
    asyncio.run(run())


def test_direct_resource_upload_cannot_bypass_hold(tmp_path, held):
    async def run():
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail('No HTTP')))
        service=CloudSync(SimpleNamespace(path=tmp_path/'personal.sqlite3'),None,None,None,None,client=client)
        original=tmp_path/'private.txt';original.write_text('private')
        try:
            with pytest.raises(SyncError,match='cloud_uploads_held'):
                await service.upload_resource({'id':'private','sha256':'0'*64,'bytes':7,'path':original,'filename':'private.txt','mime':'text/plain'})
            assert not service.resources
        finally:
            await client.aclose()
    asyncio.run(run())


def test_notes_hold_keeps_intake_and_receipts_untouched(tmp_path, held):
    async def run():
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail('No HTTP')))
        service=AnywhereNotes(SimpleNamespace(path=tmp_path/'personal.sqlite3'),None,
            SimpleNamespace(read=lambda: {'enabled': True}),client=client)
        try:
            assert (await service.sync_once())['uploads_held']
            with pytest.raises(NotesSyncError,match='cloud_uploads_held'):
                await service._request({},'POST','/ack',{'id':'private'})
        finally:
            await client.aclose()
    asyncio.run(run())


def test_paid_hold_precedes_credentials_cost_reservation_and_http(held):
    async def run():
        transport=DeepSeekTransport(httpx.MockTransport(lambda _: pytest.fail('No paid HTTP')))
        with pytest.raises(ExtractionFailure) as caught:
            await transport.request({},None,None,before_attempt=lambda: pytest.fail('No reservation'))
        assert caught.value.code=='paid_analysis_paused' and caught.value.attempts==0
    asyncio.run(run())


def test_paid_hold_survives_cloud_release_and_prevents_manual_model_override(held):
    _,write=held
    write(cloud=False)
    at=datetime(2026,11,4,4,tzinfo=timezone.utc)
    policy=SchedulerPolicy(WeChatConfig(enabled=True),clock=lambda: at)
    assert policy.authorize(operation='scan').allowed
    for operation in ('llm','vision'):
        assert policy.authorize(operation=operation).reason=='paid_analysis_paused'


def test_bark_cannot_send_source_content_during_local_recovery(held):
    from server.notifications.bark import BarkClient
    client=BarkClient(None,transport=httpx.MockTransport(lambda _: pytest.fail('No push HTTP')))
    assert client._once({'body':'private original'}).error=='cloud_uploads_held'


def test_hold_activated_after_client_creation_still_blocks_paid_request(held):
    _,write=held
    write(cloud=False,paid=False)
    transport=DeepSeekTransport(httpx.MockTransport(lambda _: pytest.fail('No paid HTTP')))
    write()
    async def run():
        with pytest.raises(ExtractionFailure,match='付费分析'):
            await transport.request({},SimpleNamespace(base_url='https://example.test',timeout=1),SecretStr('test'))
    asyncio.run(run())
