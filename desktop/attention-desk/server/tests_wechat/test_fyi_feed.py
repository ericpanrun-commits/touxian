"""FYI has a separate, reversible feed lifecycle; all providers are mocks."""
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.extraction_models import ConfirmationInput
from server.notifications.config import BarkConfig
from server.notifications.wechat import WeChatBarkDispatcher
from server.personal_store import PersonalError
from server.review_store import ReviewStore
from server.tests_wechat.test_workbench import populated,confirm_one
from server.wechat_collect.fyi import feed,mutate,conversion_preview,instant
from server.wechat_collect.inbox import list_candidates
from server.wechat_collect.api import router


def sample(personal):
    service,clock,calls=populated(personal)
    at=clock[0]
    return service,clock,calls,feed(personal,at=at)['items'][0]


def request(preview):
    return ConfirmationInput(expected_version=preview['version'],extraction_id=preview['extraction_id'],acknowledged_warnings=True,events=preview['draft']['events'])


def test_fyi_source_time_sort_pagination_and_automatic_seven_day_archive(personal):
    service,clock,calls,item=sample(personal)
    anchor=instant(item['source_at'])  # Unknown source time falls back to captured classification time.
    assert feed(personal,at=anchor+timedelta(days=6))['total']==1
    assert feed(personal,at=anchor+timedelta(days=7))['total']==0
    archived=feed(personal,view='archived',at=anchor+timedelta(days=7))['items'][0]
    assert archived['archive_reason']=='expired' and archived['fyi_state']=='archived'
    assert list_candidates(personal)['total']==1
    assert len(calls)==1


def test_pin_retains_old_fyi_for_thirty_days_then_archives(personal):
    service,clock,calls,item=sample(personal)
    pinned=mutate(personal,item['id'],'pin',item['fyi_version'],at=clock[0])['item']
    assert feed(personal,at=clock[0]+timedelta(days=29))['total']==1
    assert feed(personal,at=clock[0]+timedelta(days=30))['total']==0
    archived=feed(personal,view='archived',at=clock[0]+timedelta(days=30))['items'][0]
    assert archived['pinned_until'] is None
    assert pinned['fyi_version']>item['fyi_version']


@pytest.mark.parametrize('action',['archive','ignore'])
def test_manual_archive_ignore_and_undo_do_not_change_ai_or_sources(personal,action):
    service,clock,calls,item=sample(personal)
    with personal.connection() as c:
        original=[tuple(r) for r in c.execute('SELECT * FROM raw_source_block')]
        analysis=c.execute('SELECT data_json FROM wechat_candidate WHERE id=?',(item['id'],)).fetchone()[0]
    saved=mutate(personal,item['id'],action,item['fyi_version'],at=clock[0])['item']
    assert feed(personal,at=clock[0])['total']==0
    restored=mutate(personal,item['id'],'restore',saved['fyi_version'],at=clock[0])['item']
    assert restored['fyi_state']=='active' and feed(personal,at=clock[0])['total']==1
    with personal.connection() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM raw_source_block')]==original
        assert c.execute('SELECT data_json FROM wechat_candidate WHERE id=?',(item['id'],)).fetchone()[0]==analysis
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0


def test_stale_state_is_a_conflict_and_cannot_overwrite_newer_pin(personal):
    service,clock,calls,item=sample(personal)
    mutate(personal,item['id'],'pin',item['fyi_version'],at=clock[0])
    with pytest.raises(PersonalError) as error:mutate(personal,item['id'],'ignore',item['fyi_version'],at=clock[0])
    assert error.value.code=='conflict' and feed(personal,at=clock[0])['total']==1


def test_feed_sorts_by_cited_source_time_and_pages_without_using_arrival_order(personal):
    import asyncio
    from datetime import datetime
    from server.tests_wechat.test_pipeline import setup_service,scan,output_for
    from server.wechat_collect.models import Observation,ObservedMessage
    current=datetime.fromisoformat('2026-10-23T10:00:00+08:00')
    messages=(ObservedMessage('考试必须使用校园网。',source_time='2026-10-22T10:00:00+08:00',time_reliability='reliable'),
              ObservedMessage('通知：成绩查询方式已经开放。',source_time='2026-10-23T09:00:00+08:00',time_reliability='reliable'))
    def response(payload):
        result=output_for(payload);result['items'][0].update(category='notice',attention_level='fyi',action_required=False,actions=[])
        return result
    service,clock,calls=setup_service(personal,[Observation('test','unverified',messages,True,True)],time=current.isoformat(),response=response)
    async def run():await scan(service);await service.process_queue()
    asyncio.run(run())
    full=feed(personal,limit=20,at=current)
    assert full['total']==2
    assert full['items'][0]['source_at']>full['items'][1]['source_at']
    assert feed(personal,limit=1,offset=1,at=current)['items'][0]['id']==full['items'][1]['id']


def test_manual_conversion_preserves_source_is_idempotent_and_never_changes_classification(personal):
    service,clock,calls,item=sample(personal)
    preview=conversion_preview(personal,item['id'],at=clock[0])
    assert preview['draft']['events'][0]['actions']==[]
    payload=request(preview)
    reviews=ReviewStore(personal)
    with pytest.raises(PersonalError) as error:reviews.confirm(item['snapshot_id'],payload)
    assert error.value.code=='attention_not_action'
    saved=reviews.confirm(item['snapshot_id'],payload,fyi_candidate_id=item['id'],fyi_version=preview['fyi_version'])
    again=reviews.confirm(item['snapshot_id'],payload,fyi_candidate_id=item['id'],fyi_version=preview['fyi_version'])
    assert len(saved['events'])==1 and saved['events'][0]['id']==again['events'][0]['id']
    assert saved['events'][0]['raw_snapshot_id']==item['snapshot_id']
    assert saved['events'][0]['category']=='notice'
    assert feed(personal,at=clock[0])['total']==0
    archived=feed(personal,view='archived',at=clock[0])['items'][0]
    assert archived['converted_event_id']==saved['events'][0]['id']
    assert archived['analysis']['attention_level']=='fyi'
    assert list_candidates(personal)['total']==1 and len(calls)==1
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM task').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM deadline').fetchone()[0]==0


def test_conversion_cannot_include_action_sibling_and_preserves_sibling_draft(personal):
    service,clock,calls,item=sample(personal)
    reviews=ReviewStore(personal)
    action=list_candidates(personal)['items'][0]
    bundle=reviews.get(action['snapshot_id'])
    sibling={'id':action['model_item_id'],'title':'尚未确认的用户草稿','selected':True}
    reviews.save_draft(action['snapshot_id'],bundle['version'],{'events':[sibling]})
    preview=conversion_preview(personal,item['id'],at=clock[0]);payload=request(preview)
    bad=payload.model_copy(deep=True);bad.events[0].id=action['model_item_id']
    with pytest.raises(PersonalError):reviews.confirm(item['snapshot_id'],bad,fyi_candidate_id=item['id'],fyi_version=preview['fyi_version'])
    bad=payload.model_copy(deep=True)
    sibling_event=bad.events[0].model_copy(update={'id':action['model_item_id'],'selected':False})
    bad.events.append(sibling_event)
    with pytest.raises(PersonalError):reviews.confirm(item['snapshot_id'],bad,fyi_candidate_id=item['id'],fyi_version=preview['fyi_version'])
    reviews.confirm(item['snapshot_id'],payload,fyi_candidate_id=item['id'],fyi_version=preview['fyi_version'])
    saved=reviews.get(action['snapshot_id'])
    assert sibling in saved['draft']['events']
    assert list_candidates(personal)['items'][0]['id']==action['id']


def test_conversion_still_works_after_action_sibling_was_confirmed(personal):
    service,clock,calls,item=sample(personal)
    confirm_one(personal,list_candidates(personal)['items'][0])
    preview=conversion_preview(personal,item['id'],at=clock[0])
    saved=ReviewStore(personal).confirm(item['snapshot_id'],request(preview),fyi_candidate_id=item['id'],fyi_version=preview['fyi_version'])
    assert len(saved['events'])==1
    with personal.connection() as c:assert c.execute('SELECT count(*) FROM event').fetchone()[0]==2
    assert list_candidates(personal)['total']==0


def test_fyi_feed_operations_and_conversion_never_dispatch_bark(personal):
    service,clock,calls,item=sample(personal)
    # Remove the unrelated Action from eligibility; exercise FYI alone with Bark enabled.
    with personal.connection(write=True) as c:
        c.execute("UPDATE wechat_candidate SET status='ignored' WHERE attention_level='action'")
        c.execute("UPDATE wechat_bark_settings SET enabled=1,activated_at='2020-01-01'")
    sent=[]
    dispatcher=WeChatBarkDispatcher(personal,config=BarkConfig(True,'https://bark.invalid','synthetic-only'),transport=httpx.MockTransport(lambda r:sent.append(r) or httpx.Response(200,json={'code':200})),clock=lambda:clock[0])
    dispatcher.tick()
    pinned=mutate(personal,item['id'],'pin',item['fyi_version'],at=clock[0])['item'];dispatcher.tick()
    preview=conversion_preview(personal,item['id'],at=clock[0])
    ReviewStore(personal).confirm(item['snapshot_id'],request(preview),fyi_candidate_id=item['id'],fyi_version=preview['fyi_version']);dispatcher.tick()
    assert sent==[]
    with personal.connection() as c:assert c.execute('SELECT count(*) FROM wechat_bark_notification').fetchone()[0]==0


def test_feed_and_conversion_api_have_desktop_origin_and_strict_revision_boundaries(personal):
    service,clock,calls,item=sample(personal)
    app=FastAPI();app.include_router(router);app.state.wechat_service=service
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        assert client.get('/api/personal/wechat/fyi').status_code==200
        endpoint='/api/personal/wechat/fyi/'+item['id']
        assert client.post(endpoint+'/state',json={'action':'ignore','expected_version':item['fyi_version']},headers={'Origin':'https://evil.invalid'}).status_code==403
        preview=client.get(endpoint+'/conversion').json()
        body={**request(preview).model_dump(),'fyi_version':preview['fyi_version']}
        result=client.post(endpoint+'/convert',json=body,headers={'Origin':'http://127.0.0.1:8765'})
        assert result.status_code==200,result.text
        assert client.post(endpoint+'/convert',json=body,headers={'Origin':'http://127.0.0.1:8765'}).json()['events'][0]['id']==result.json()['events'][0]['id']
