"""Calendar, Today/Upcoming, RFC 5545 and explicit source-update acceptance."""
import json
from datetime import date,datetime,timedelta,timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from icalendar import Calendar

from server.personal_models import DeadlineInput,EvidenceInput
from server.personal_api import router as personal_router
from server.personal_store import PersonalError
from server.planning.api import router as planning_router
from server.planning.ics import export,public_url
from server.planning.query import PlanningQuery
from server.planning.source_update import comparison
from server.planning_test_helpers import AT,Notifier,event_with_deadline,new_store
from server.reminders.api import router as reminder_router
from server.reminders.service import ReminderService
from server.review_store import ReviewStore


@pytest.fixture
def store(tmp_path):
    return new_store(tmp_path/'planning.sqlite3')


@pytest.fixture
def client(store):
    app = FastAPI()
    app.state.personal_store = store
    app.state.reminder_service = ReminderService(store,Notifier(),clock=lambda:AT)
    app.include_router(personal_router)
    app.include_router(planning_router)
    app.include_router(reminder_router)
    with TestClient(app) as client:
        yield client


def add_date(store,event,value,precision='date',**changes):
    return store.add_deadline(event['id'],DeadlineInput(expected_version=event['version'],label='另一时间节点',value=value,precision=precision,**changes))


def test_calendar_date_only_and_exact_datetime_are_distinct(store):
    event = event_with_deadline(store)
    add_date(store,event,'2026-10-12T14:30','minute',timezone='Asia/Shanghai')
    month = PlanningQuery(store).month(2026,10,at=AT)
    assert month['total']==2
    all_day = next(item for item in month['items'] if item['precision']=='date')
    timed = next(item for item in month['items'] if item['precision']=='minute')
    assert all_day['all_day'] and all_day['start_at'] is None
    assert not timed['all_day'] and timed['start_at'].endswith('+08:00')
    assert {item['event_id'] for item in month['items']}=={event['id']}


def test_calendar_update_has_same_identity_and_no_old_day(store):
    event = event_with_deadline(store)
    deadline_id = event['deadlines'][0]['id']
    store.edit_deadline(event['id'],deadline_id,DeadlineInput(expected_version=event['version'],label='提交',value='2026-11-15'))
    assert PlanningQuery(store).month(2026,10,at=AT)['total']==0
    assert PlanningQuery(store).month(2026,11,at=AT)['items'][0]['id']==deadline_id


@pytest.mark.parametrize('status',['done','cancelled'])
def test_terminal_excluded_by_default_and_explicitly_visible(store,status):
    event = event_with_deadline(store,'2026-10-09')
    store.change_status(event['id'],event['version'],status,True)
    query = PlanningQuery(store)
    assert query.month(2026,10,at=AT)['total']==0
    assert query.today(at=AT)['total']==0
    assert query.upcoming(at=AT)['total']==0
    assert query.month(2026,10,at=AT,include_terminal=True)['items'][0]['event_status']==status


def test_unconfirmed_unknown_and_uncertain_calendar_excluded(store):
    event_with_deadline(store,confirmed_by_user=False)
    event_with_deadline(store,confirmed_by_user=False,uncertain=True)
    event_with_deadline(store,'','unknown',confirmed_by_user=False)
    assert PlanningQuery(store).month(2026,10,at=AT)['total']==0


def test_calendar_year_month_boundaries_and_inclusive_date_range(store):
    event_with_deadline(store,'2026-12-31','range',end_value='2027-01-02',range_precision='date')
    query = PlanningQuery(store)
    for year,month in ((2026,12),(2027,1)):
        item = query.month(year,month,at=AT)['items'][0]
        assert item['start_date']=='2026-12-31' and item['end_date']=='2027-01-02'
    assert query.month(2026,11,at=AT)['total']==0
    assert query.month(2027,2,at=AT)['total']==0


def test_datetime_timezone_projects_to_display_day_without_changing_fact(store,monkeypatch):
    from zoneinfo import ZoneInfo
    import server.planning.query as query_module
    event = event_with_deadline(store,'2026-11-01T00:30','minute')
    monkeypatch.setattr(query_module,'system_zone',lambda:ZoneInfo('America/Los_Angeles'))
    item = PlanningQuery(store).month(2026,10,at=AT)['items'][0]
    assert item['start_date']=='2026-10-31'
    assert store.get_event(event['id'])['deadlines'][0]['value']=='2026-11-01T00:30'


def test_today_orders_overdue_today_and_reminder_reason_with_remaining_tasks(store):
    future = event_with_deadline(store,tasks=2)
    today = event_with_deadline(store,'2026-10-09',tasks=1)
    overdue = event_with_deadline(store,'2026-10-07',tasks=1)
    service = ReminderService(store,Notifier(),clock=lambda:AT)
    service.tick()
    result = PlanningQuery(store).today(at=AT)
    assert [row['event_id'] for row in result['items']]==[overdue['id'],today['id'],future['id']]
    assert result['items'][0]['reasons']==['已逾期 2 天']
    assert '今天截止' in result['items'][1]['reasons']
    assert result['items'][2]['reasons']==['今天有提醒']
    assert len(result['items'][2]['tasks'])==2
    assert store.get_event(overdue['id'])['status']=='needs_review'


@pytest.mark.parametrize('days,expected',[(7,2),(14,3),(30,4)])
def test_upcoming_windows_sorted_and_multiple_deadlines(store,days,expected):
    event = event_with_deadline(store,'2026-10-10')
    event = add_date(store,event,'2026-10-15')
    event = add_date(store,event,'2026-10-20')
    add_date(store,event,'2026-11-01')
    result = PlanningQuery(store).upcoming(days,at=AT)
    assert result['total']==expected
    assert [item['value'] for item in result['items']]==sorted(item['value'] for item in result['items'])
    assert all(item['event_id']==event['id'] for item in result['items'])


def test_today_at_midnight_and_month_boundary(store):
    event_with_deadline(store,'2026-10-31')
    at = datetime(2026,10,31,16,tzinfo=timezone.utc)
    item = PlanningQuery(store).today(at=at)['items'][0]
    assert item['state']=='overdue' and item['overdue_days']==1


def test_ics_valid_calendar_and_date_event_without_fake_time(store):
    event_with_deadline(store)
    payload = export(store,at=AT)
    calendar = Calendar.from_ical(payload)
    entry = calendar.walk('VEVENT')[0]
    assert calendar.name=='VCALENDAR' and str(calendar['VERSION'])=='2.0'
    assert entry.decoded('DTSTART')==date(2026,10,12)
    assert entry.decoded('DTEND')==date(2026,10,13)
    assert b'DTSTART;VALUE=DATE:20261012' in payload
    assert b'2359' not in payload
    assert entry.decoded('DTSTAMP').tzinfo and entry.decoded('LAST-MODIFIED').tzinfo


def test_ics_datetime_is_correct_aware_utc(store):
    event_with_deadline(store,'2026-10-12T14:30','minute')
    payload = export(store,at=AT)
    entry = Calendar.from_ical(payload).walk('VEVENT')[0]
    assert entry.decoded('DTSTART')==datetime(2026,10,12,6,30,tzinfo=timezone.utc)
    assert b'DTSTART:20261012T063000Z' in payload


def test_ics_date_and_minute_ranges_keep_end_semantics(store):
    event = event_with_deadline(store,'2026-10-12','range',end_value='2026-10-14',range_precision='date')
    add_date(store,event,'2026-10-15T09:00','range',range_precision='minute',end_value='2026-10-15T11:00',timezone='Asia/Shanghai')
    entries = Calendar.from_ical(export(store,at=AT)).walk('VEVENT')
    day = next(entry for entry in entries if not isinstance(entry.decoded('DTSTART'),datetime))
    timed = next(entry for entry in entries if isinstance(entry.decoded('DTSTART'),datetime))
    assert day.decoded('DTEND')==date(2026,10,15)
    assert timed.decoded('DTEND')-timed.decoded('DTSTART')==timedelta(hours=2)


def test_ics_uid_stable_across_export_restart_and_date_change(store):
    event = event_with_deadline(store)
    first = Calendar.from_ical(export(store,at=AT)).walk('VEVENT')[0]
    store.edit_deadline(event['id'],event['deadlines'][0]['id'],DeadlineInput(expected_version=event['version'],label='材料提交',value='2026-10-15'))
    restarted = new_store(store.path)
    updated = Calendar.from_ical(export(restarted,at=AT+timedelta(hours=1))).walk('VEVENT')[0]
    assert first['UID']==updated['UID']
    assert updated.decoded('DTSTART')==date(2026,10,15)
    assert updated['SEQUENCE']>first['SEQUENCE']


def test_ics_unicode_escaping_fold_description_and_source(store):
    event = event_with_deadline(store,tasks=2)
    title = '中文标题，含逗号,分号;换行\n'+'报名事项'*30
    store.edit_event(event['id'],event['version'],{'title':title[:200],'summary':'','category':'其他'})
    payload = export(store,at=AT)
    entry = Calendar.from_ical(payload).walk('VEVENT')[0]
    assert str(entry['SUMMARY'])=='【材料提交】'+title[:200]
    assert b'\\,' in payload and b'\\;' in payload and b'\\n' in payload
    assert '\r\n ' in payload.decode()
    assert '任务 1' in str(entry['DESCRIPTION']) and '手动粘贴' in str(entry['DESCRIPTION'])
    assert all(len(line)<=75 for line in payload.split(b'\r\n'))


def test_ics_never_contains_local_paths_source_original_or_obvious_secrets(store):
    event = event_with_deadline(store)
    store.edit_event(event['id'],event['version'],{'title':'C:\\Users\\anon\\private.pdf\nAPI_KEY=synthetic-secret-value','summary':'private raw body','category':'其他'})
    with store.connection(write=True) as db:
        db.execute('UPDATE source SET name=?',('\\\\computer\\private\\source',))
        db.execute('UPDATE raw_item SET canonical_url=?',('https://example.edu/notice?pairing_token=synthetic-secret-value',))
    payload = export(store,at=AT).decode()
    for forbidden in ('C:\\Users','computer','private.pdf','synthetic-secret-value','private raw body','请于10月12日前'):
        assert forbidden not in payload
    assert 'URL:' not in payload


@pytest.mark.parametrize('value',[
    'http://127.0.0.1:8765/settings','http://localhost/x','http://[::1]/x',
    'http://192.168.1.195/x','http://school.local/x','file:///C:/private/a.pdf',
    'https://user:pass@example.edu/x','https://example.edu/x?api_key=secret',
    'http://2130706433/x','http://127.1/x',
])
def test_ics_private_credential_or_nonpublic_urls_excluded(value):
    assert public_url(value) is None


def test_ics_safe_public_source_and_export_scope(store):
    first = event_with_deadline(store)
    second = event_with_deadline(store,'2026-10-15')
    event_with_deadline(store,'2026-09-01')
    with store.connection(write=True) as db:
        db.execute('UPDATE raw_item SET canonical_url=? WHERE id=?',('https://www.example.edu/notice/123',first['raw_item_id']))
    assert len(Calendar.from_ical(export(store,at=AT,future=True)).walk('VEVENT'))==2
    entries = Calendar.from_ical(export(store,event_id=first['id'],at=AT)).walk('VEVENT')
    assert len(entries)==1 and str(entries[0]['URL'])=='https://www.example.edu/notice/123'
    entries = Calendar.from_ical(export(store,deadline_id=second['deadlines'][0]['id'],at=AT)).walk('VEVENT')
    assert len(entries)==1


def test_ics_cancelled_explicit_update_retains_uid_and_marks_cancelled(store):
    event = event_with_deadline(store)
    first = Calendar.from_ical(export(store,at=AT)).walk('VEVENT')[0]
    store.change_status(event['id'],event['version'],'cancelled',True)
    assert not Calendar.from_ical(export(store,at=AT)).walk('VEVENT')
    updated = Calendar.from_ical(export(store,at=AT,include_terminal=True)).walk('VEVENT')[0]
    assert updated['UID']==first['UID'] and str(updated['STATUS'])=='CANCELLED'


def test_source_update_is_proposal_until_explicit_confirmation_then_updates_projections(store):
    event = event_with_deadline(store)
    original_snapshot = event['raw_snapshot_id']
    deadline = event['deadlines'][0]
    service = ReminderService(store,Notifier(),clock=lambda:AT)
    service.tick()
    with store.connection(write=True) as db:
        source_id = ReviewStore._new_snapshot(db,event['raw_item_id'],'截止延长至10月15日。',(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat(),None,'更新')
        proposal = comparison(db,event['raw_item_id'],source_id,{'parsed_output':{'events':[{'title':'更新通知','deadlines':[
            {'role':deadline['label'],'normalized_date':'2026-10-15','precision':'date','uncertain':False}]}]}})
    assert proposal['proposals'][0]['conflicts'][0]['old_value']=='2026-10-12'
    assert store.get_event(event['id'])['deadlines'][0]['value']=='2026-10-12'
    assert store.get_event(event['id'])['source_update']['id']==source_id
    old_uid = Calendar.from_ical(export(store,at=AT)).walk('VEVENT')[0]['UID']
    missing_ack = DeadlineInput(expected_version=event['version'],label=deadline['label'],value='2026-10-15',source_snapshot_id=source_id,evidence=EvidenceInput(quote='10月15日'))
    with pytest.raises(PersonalError,match='明确确认'):
        store.edit_deadline(event['id'],deadline['id'],missing_ack)
    approved = missing_ack.model_copy(update={'acknowledged_source_change':True})
    saved = store.edit_deadline(event['id'],deadline['id'],approved)
    assert saved['deadlines'][0]['id']==deadline['id'] and saved['deadlines'][0]['source_snapshot_id']==source_id
    assert saved['raw_snapshot_id']==original_snapshot
    assert saved['evidence'][-1]['raw_snapshot_id']==source_id
    assert saved['evidence_snapshots'][source_id]['original_text']=='截止延长至10月15日。'
    service.repository.reconcile(at=AT)
    assert len([row for row in service.repository.center()['items'] if row['status']=='fired'])==1
    assert PlanningQuery(store).month(2026,10,at=AT)['items'][0]['value']=='2026-10-15'
    assert Calendar.from_ical(export(store,at=AT)).walk('VEVENT')[0]['UID']==old_uid


def test_source_update_evidence_cannot_reference_another_raw_item(store):
    first = event_with_deadline(store)
    second = event_with_deadline(store)
    payload = DeadlineInput(expected_version=first['version'],label='提交',value='2026-10-15',
        source_snapshot_id=second['raw_snapshot_id'],acknowledged_source_change=True,evidence=EvidenceInput(quote='10月12日'))
    with pytest.raises(PersonalError,match='不属于'):
        store.edit_deadline(first['id'],first['deadlines'][0]['id'],payload)
    assert store.get_event(first['id'])['version']==first['version']


def test_source_update_actual_extraction_pipeline_preserves_facts_until_approval(store):
    import asyncio
    from server.extraction_service import ExtractionService
    from server.test_extraction_review import FixtureProvider,TestSettings,candidate,finish
    event = event_with_deadline(store,tasks=2)
    deadline = event['deadlines'][0]
    event = store.edit_deadline(event['id'],deadline['id'],DeadlineInput(
        expected_version=event['version'],label=deadline['label'],value=deadline['value'],
        evidence=EvidenceInput(quote='10月12日')))
    before = store.get_event(event['id'])
    text = '延期至2026年10月15日前提交材料。'
    with store.connection(write=True) as db:
        snapshot_id = ReviewStore._new_snapshot(db,event['raw_item_id'],text,
            (datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat(),None,'延期通知')
    output = candidate(text,dates=[('2026年10月15日前','2026-10-15')],actions=['提交材料'])
    output['events'][0]['deadlines'][0]['role'] = deadline['label']
    output['events'][0]['notice_kind'] = 'update'
    provider = FixtureProvider(output)
    async def extract():
        service = ExtractionService(store,TestSettings(),provider)
        try:
            await service.start(snapshot_id)
            await finish(service)
        finally:
            await service.close()
    asyncio.run(extract())
    bundle = ReviewStore(store).get(snapshot_id)
    assert provider.calls==1 and bundle['extraction']['status']=='needs_review'
    assert bundle['source_update']['proposals'][0]['normalized_date']=='2026-10-15'
    assert bundle['source_update']['proposals'][0]['conflicts'][0]['deadline_id']==deadline['id']
    unchanged = store.get_event(event['id'])
    for field in ('deadlines','tasks','evidence','version'):
        assert unchanged[field]==before[field]
    repository = ReminderService(store,Notifier(),clock=lambda:AT).repository
    repository.reconcile(at=AT)
    old_reminders = repository.center()['items']
    saved = store.edit_deadline(event['id'],deadline['id'],DeadlineInput(
        expected_version=event['version'],label=deadline['label'],value='2026-10-15',
        source_snapshot_id=snapshot_id,acknowledged_source_change=True,
        evidence=EvidenceInput(quote='2026年10月15日前')))
    assert saved['deadlines'][0]['id']==deadline['id']
    assert saved['tasks']==before['tasks']
    assert all(value in saved['evidence'] for value in before['evidence'])
    repository.reconcile(at=AT)
    current = repository.center()['items']
    assert all(next(row for row in current if row['id']==old['id'])['status']=='cancelled' for old in old_reminders)
    assert any(row['status']=='scheduled' and row['id'] not in {old['id'] for old in old_reminders} for row in current)
    assert PlanningQuery(store).month(2026,10,at=AT)['items'][0]['value']=='2026-10-15'
    restarted = new_store(store.path)
    assert restarted.get_event(event['id'])==saved


def test_desktop_api_calendar_ics_settings_actions_and_test_notification(client,store):
    event = event_with_deadline(store)
    assert client.get('/api/personal/planning/calendar?year=2026&month=10').status_code==200
    assert client.get('/api/personal/planning/upcoming?days=8').status_code==422
    assert client.get('/api/personal/planning/calendar?year=2026&month=13').status_code==422
    ics = client.get('/api/personal/planning/export.ics',params={'event_id':event['id'],'future':'false'})
    assert ics.status_code==200 and ics.headers['content-type'].startswith('text/calendar')
    assert len(Calendar.from_ical(ics.content).walk('VEVENT'))==1
    settings = client.get('/api/personal/reminders/settings').json()
    assert settings['privacy']=='title' and settings['backend_required']
    assert client.post('/api/personal/reminders/test',json={}).json()['accepted']
    assert client.post('/api/personal/reminders/test',data={}).status_code==422
    bad = client.put('/api/personal/reminders/settings',json={'expected_version':1,'date_offsets':[99]})
    assert bad.status_code==422
    good = client.put('/api/personal/reminders/settings',json={'expected_version':1,'enabled':False})
    assert good.status_code==200
    assert client.put('/api/personal/reminders/settings',json={'expected_version':1}).status_code==409
    assert client.get('/api/personal/planning/export.ics?deadline_id=bad').status_code==422


def test_calendar_and_export_do_not_read_files_call_ai_or_network(store,monkeypatch):
    import socket
    from pathlib import Path
    event_with_deadline(store)
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('network called'))
    monkeypatch.setattr(Path,'read_bytes',lambda *a,**k:pytest.fail('attachment read'))
    assert PlanningQuery(store).today(at=AT)
    assert PlanningQuery(store).month(2026,10,at=AT)['total']==1
    assert len(Calendar.from_ical(export(store,at=AT)).walk('VEVENT'))==1
