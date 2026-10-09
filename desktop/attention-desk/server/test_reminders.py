"""Persistent policy, scheduler, shutdown, failure and fact-change acceptance."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone

import pytest

from server.personal_models import DeadlineInput
from server.personal_store import PersonalError
from server.planning_test_helpers import AT,Notifier,event_with_deadline,new_store
from server.reminders.policy import ReminderSettingsInput,schedules
from server.reminders.service import ReminderService
from server.reminders.store import ReminderStore
from server.reminders.windows import content


@pytest.fixture
def store(tmp_path):
    return new_store(tmp_path/'reminders.sqlite3')


def rows(repository):
    return repository.center()['items']


@pytest.mark.parametrize('kind,expected',[
    ('date:3:09:00','2026-10-09T01:00:00.000000+00:00'),
    ('date:1:09:00','2026-10-11T01:00:00.000000+00:00'),
    ('date:0:09:00','2026-10-12T01:00:00.000000+00:00'),
])
def test_default_date_offsets_preserve_fact(store,kind,expected):
    event = event_with_deadline(store)
    repository = ReminderStore(store)
    repository.reconcile(at=AT)
    assert next(row for row in rows(repository) if row['reminder_type']==kind)['scheduled_at']==expected
    assert store.get_event(event['id'])['deadlines'][0]['value']=='2026-10-12'


@pytest.mark.parametrize('offset,expected',[(24,'2026-10-09T01:00:00.000000+00:00'),(2,'2026-10-09T23:00:00.000000+00:00')])
def test_default_datetime_elapsed_offsets(store,offset,expected):
    event_with_deadline(store,'2026-10-10T09:00','minute')
    repository = ReminderStore(store)
    repository.reconcile(at=AT)
    assert next(row for row in rows(repository) if row['reminder_type']==f'minute:{offset}')['scheduled_at']==expected


def test_duplicate_ticks_and_restart_never_refire(store):
    event_with_deadline(store)
    notifier = Notifier()
    service = ReminderService(store,notifier,clock=lambda:AT)
    for _ in range(10):
        service.tick()
    restarted = ReminderService(new_store(store.path),notifier,clock=lambda:AT+timedelta(minutes=1))
    restarted.tick()
    assert len(notifier.calls)==1
    assert len([row for row in rows(service.repository) if row['status']=='fired'])==1


@pytest.mark.parametrize('value,precision',[('2026-10-12','date'),('2026-10-09T11:00','minute')])
def test_system_timezone_default_delivers_without_projection_changing_fact_identity(store,monkeypatch,value,precision):
    from zoneinfo import ZoneInfo
    from server.planning import time
    from server.planning.query import PlanningQuery
    from server.reminders.policy import fact_key
    monkeypatch.setattr(time,'system_zone',lambda:ZoneInfo('Asia/Shanghai'))
    event = event_with_deadline(store,value,precision)
    with store.connection(write=True) as db:
        db.execute('UPDATE deadline SET timezone=NULL WHERE event_id=?',(event['id'],))
    fact = store.get_event(event['id'])['deadlines'][0]
    projected = PlanningQuery(store).deadlines(at=AT)[0]
    assert projected['timezone'] is None and projected['resolved_timezone']=='Asia/Shanghai'
    assert fact_key(fact)==fact_key(projected)
    notifier = Notifier()
    service = ReminderService(store,notifier,clock=lambda:AT)
    assert service.tick()==1 and len(notifier.calls)==1
    assert rows(service.repository)[0]['status']=='fired'
    assert store.get_event(event['id'])['deadlines'][0]['timezone'] is None


def test_atomic_claim_across_concurrent_schedulers(store):
    event_with_deadline(store)
    repository = ReminderStore(store)
    repository.reconcile(at=AT)
    with ThreadPoolExecutor(max_workers=4) as executor:
        claims = list(executor.map(lambda _:ReminderStore(store).claim(at=AT),range(4)))
    assert sum(map(len,claims))==1


def test_restart_before_fire_restores_same_schedule_ids(store):
    event_with_deadline(store)
    service = ReminderService(store,Notifier(),clock=lambda:AT-timedelta(minutes=5))
    assert service.tick()==0
    ids = {row['id'] for row in rows(service.repository)}
    restarted = ReminderService(new_store(store.path),Notifier(),clock=lambda:AT)
    assert restarted.tick()==1
    assert {row['id'] for row in rows(restarted.repository)}==ids


@pytest.mark.parametrize('delay,expected',[(timedelta(hours=1,minutes=30),1),(timedelta(hours=6),1),(timedelta(hours=6,seconds=1),0),(timedelta(days=30),0)])
def test_sleep_wake_and_grace_boundaries(store,delay,expected):
    event_with_deadline(store)
    repository = ReminderStore(store)
    repository.reconcile(at=AT-timedelta(minutes=1))
    notifier = Notifier()
    service = ReminderService(new_store(store.path),notifier,clock=lambda:AT+delay)
    service.tick()
    assert len(notifier.calls)==expected
    service.tick()
    assert len(notifier.calls)==expected


@pytest.mark.parametrize('action,delta',[('10m',timedelta(minutes=10)),('1h',timedelta(hours=1)),('tomorrow',timedelta(days=1))])
def test_snooze_persists_and_fires_once_without_completing_facts(store,action,delta):
    event = event_with_deadline(store,tasks=2)
    notifier = Notifier()
    service = ReminderService(store,notifier,clock=lambda:AT)
    service.tick()
    fired = next(row for row in rows(service.repository) if row['status']=='fired')
    snoozed = service.repository.act(fired['id'],action,at=AT,expected_generation=0)
    assert datetime.fromisoformat(snoozed['snoozed_until'])==AT+delta
    restarted = ReminderService(new_store(store.path),notifier,clock=lambda:AT+delta)
    restarted.tick()
    restarted.tick()
    assert len(notifier.calls)==2
    saved = store.get_event(event['id'])
    assert saved['status']=='needs_review' and all(task['status']=='todo' for task in saved['tasks'])
    assert restarted.repository.get(fired['id'])['generation']==1


def test_dismiss_only_closes_current_reminder(store):
    event = event_with_deadline(store,tasks=1)
    service = ReminderService(store,Notifier(),clock=lambda:AT)
    service.tick()
    fired = next(row for row in rows(service.repository) if row['status']=='fired')
    service.repository.act(fired['id'],'dismiss',at=AT)
    service.tick()
    assert service.repository.get(fired['id'])['status']=='dismissed'
    saved = store.get_event(event['id'])
    assert saved['status']=='needs_review' and saved['tasks'][0]['status']=='todo'
    assert len([row for row in rows(service.repository) if row['status']=='scheduled'])==2


@pytest.mark.parametrize('terminal',['done','cancelled'])
def test_terminal_cancel_restore_future_only(store,terminal):
    event = event_with_deadline(store,tasks=1)
    repository = ReminderStore(store)
    repository.reconcile(at=AT-timedelta(minutes=1))
    event = store.change_status(event['id'],event['version'],terminal,True)
    repository.reconcile(at=AT)
    assert all(row['status']=='cancelled' for row in rows(repository))
    store.restore_event(event['id'],event['version'])
    repository.reconcile(at=AT+timedelta(minutes=2))
    assert len([row for row in rows(repository) if row['status']=='scheduled'])==2
    assert not repository.claim(at=AT+timedelta(minutes=2))


def test_deadline_edit_cancels_future_and_keeps_fired_history(store):
    event = event_with_deadline(store)
    service = ReminderService(store,Notifier(),clock=lambda:AT)
    service.tick()
    old_ids = {row['id'] for row in rows(service.repository)}
    date = event['deadlines'][0]
    store.edit_deadline(event['id'],date['id'],DeadlineInput(expected_version=event['version'],label=date['label'],value='2026-10-15'))
    service.repository.reconcile(at=AT)
    old = [row for row in rows(service.repository) if row['id'] in old_ids]
    assert sorted(row['status'] for row in old)==['cancelled','cancelled','fired']
    assert len([row for row in rows(service.repository) if row['status']=='scheduled'])==3


def test_task_completion_recalculates_without_auto_completing_event(store):
    event = event_with_deadline(store,tasks=1)
    repository = ReminderStore(store)
    repository.reconcile(at=AT-timedelta(minutes=1))
    store.edit_task(event['id'],event['tasks'][0]['id'],event['version'],{'status':'done'})
    repository.reconcile(at=AT)
    assert all(row['status']=='cancelled' for row in rows(repository))
    assert store.get_event(event['id'])['status']=='needs_review'


def test_unconfirmed_unknown_and_uncertain_do_not_remind(store):
    event_with_deadline(store,confirmed_by_user=False)
    event_with_deadline(store,confirmed_by_user=False,uncertain=True)
    event_with_deadline(store,'','unknown',confirmed_by_user=False)
    service = ReminderService(store,Notifier(),clock=lambda:AT)
    assert service.tick()==0 and rows(service.repository)==[]


def test_resolving_uncertainty_enables_schedule(store):
    event = event_with_deadline(store,confirmed_by_user=False,uncertain=True)
    service = ReminderService(store,Notifier(),clock=lambda:AT)
    assert service.tick()==0
    store.edit_deadline(event['id'],event['deadlines'][0]['id'],DeadlineInput(expected_version=event['version'],label='截止',value='2026-10-12'))
    assert service.tick()==1


def test_offline_scheduler_never_calls_network_ai_ocr(store,monkeypatch):
    import socket
    import server.extraction_provider as ai
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('network called'))
    monkeypatch.setattr(ai.DeepSeekProvider,'extract',lambda *a,**k:pytest.fail('AI called'))
    event_with_deadline(store)
    assert ReminderService(store,Notifier(),clock=lambda:AT).tick()==1


def test_unavailable_backend_is_visible_retried_bounded_and_safe_logs(store,caplog):
    event_with_deadline(store)
    clock = [AT]
    notifier = Notifier('backend_unavailable')
    service = ReminderService(store,notifier,clock=lambda:clock[0])
    for offset in (0,1,120,121,720,721,3600):
        clock[0] = AT+timedelta(seconds=offset)
        service.tick()
    assert len(notifier.calls)==3
    failure = next(row for row in rows(service.repository) if row['status']=='failed')
    assert failure['attempts']==3 and failure['next_attempt_at'] is None
    with store.connection() as db:
        attempts = [dict(row) for row in db.execute('SELECT * FROM reminder_attempt')]
    assert len(attempts)==3 and all(row['result']=='backend_unavailable' for row in attempts)
    assert '匿名测试事项' not in caplog.text


def test_ambiguous_crash_after_claim_never_automatically_resends(store):
    event_with_deadline(store)
    repository = ReminderStore(store)
    repository.reconcile(at=AT)
    claimed = repository.claim(at=AT)
    repository.recover_interrupted(at=AT+timedelta(minutes=1))
    notifier = Notifier()
    ReminderService(new_store(store.path),notifier,clock=lambda:AT+timedelta(minutes=2)).tick()
    assert not notifier.calls
    assert repository.get(claimed[0]['id'])['result']=='delivery_unknown'


def test_late_winrt_failure_replaces_accepted_with_visible_failure(store):
    event_with_deadline(store)
    repository = ReminderStore(store)
    repository.reconcile(at=AT)
    claimed = repository.claim(at=AT)[0]
    repository.finish(claimed,'os_accepted',at=AT)
    repository.finish(claimed,'delivery_failed',at=AT+timedelta(seconds=1))
    assert repository.get(claimed['id'])['status']=='failed'


def test_settings_disable_cancel_and_privacy_edit_does_not_duplicate(store):
    event_with_deadline(store)
    service = ReminderService(store,Notifier(),clock=lambda:AT)
    service.tick()
    service.repository.save_settings(ReminderSettingsInput(expected_version=1,privacy='hidden'),at=AT)
    assert service.tick()==0
    service.repository.save_settings(ReminderSettingsInput(expected_version=2,enabled=False),at=AT)
    assert service.tick()==0
    assert len([row for row in rows(service.repository) if row['status']=='fired'])==1
    assert len([row for row in rows(service.repository) if row['status']=='cancelled'])==2


def test_clock_rollback_does_not_repeat_fired_occurrence(store):
    event_with_deadline(store)
    notifier = Notifier()
    clock = [AT]
    service = ReminderService(store,notifier,clock=lambda:clock[0])
    service.tick()
    clock[0] -= timedelta(hours=2)
    service.tick()
    clock[0] = AT+timedelta(hours=1)
    service.tick()
    assert len(notifier.calls)==1


def test_timezone_change_recomputes_future_empty_timezone_without_new_identity(store,monkeypatch):
    from zoneinfo import ZoneInfo
    from server.planning import time
    event = event_with_deadline(store)
    with store.connection(write=True) as db:
        db.execute('UPDATE deadline SET timezone=NULL')
    repository = ReminderStore(store)
    monkeypatch.setattr(time,'system_zone',lambda:ZoneInfo('Asia/Tokyo'))
    repository.reconcile(at=AT-timedelta(days=1))
    before = {row['id']:row['scheduled_at'] for row in rows(repository)}
    monkeypatch.setattr(time,'system_zone',lambda:ZoneInfo('Europe/London'))
    repository.reconcile(at=AT-timedelta(days=1))
    after = {row['id']:row['scheduled_at'] for row in rows(repository)}
    assert set(before)==set(after) and before!=after
    assert store.get_event(event['id'])['deadlines'][0]['value']=='2026-10-12'


def test_datetime_policy_across_dst_is_elapsed_hours(store):
    value = {'id':'id','value':'2026-03-08T09:00','precision':'minute','timezone':'America/New_York','confirmed_by_user':True}
    proposals = schedules(value,ReminderStore(store).settings())
    assert next(row for row in proposals if row['reminder_type']=='minute:24')['scheduled_at']=='2026-03-07T13:00:00.000000+00:00'


def test_privacy_modes_never_use_original_notice():
    item = {'event_title':'事项标题','label':'报名','value':'2026-10-12','state':'upcoming','days_remaining':3,
            'remaining_task_count':3,'tasks':[{'title':'任务A'},{'title':'任务B'},{'title':'任务C'}],'original_text':'私人微信群原文'}
    assert '任务A' not in ''.join(content(item,'title'))
    assert '事项标题' not in ''.join(content(item,'hidden'))
    assert '任务A' in ''.join(content(item,'full')) and '任务C' not in ''.join(content(item,'full'))
    assert all('私人微信群原文' not in ''.join(content(item,mode)) for mode in ('title','hidden','full'))


def test_shutdown_wakes_wait_and_joins_cleanly(store):
    async def exercise():
        service = ReminderService(store,Notifier(),poll_seconds=30)
        await service.start()
        await asyncio.sleep(0.01)
        await asyncio.wait_for(service.close(),timeout=2)
        assert service._task is None
    asyncio.run(exercise())
