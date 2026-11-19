"""Planner task reads use only isolated synthetic SQLite data."""
from datetime import datetime, timezone
from server.personal_models import CollectionInput, DeadlineInput, TaskInput
from server.planning.query import PlanningQuery
from server.planning_test_helpers import new_store

AT = datetime(2026,11,10,4,tzinfo=timezone.utc)

def test_task_projection_filters_before_paging_and_keeps_old_unbound_tasks(tmp_path):
    store = new_store(tmp_path/'synthetic.sqlite3')
    first = store.create_collection(CollectionInput(original_text='Synthetic original',title='Old task event'))
    first = store.add_task(first['id'],TaskInput(expected_version=first['version'],title='Undated task'))
    for index in range(55):
        store.create_collection(CollectionInput(original_text=f'Synthetic {index}',title=f'New event {index}'))
    query = PlanningQuery(store)
    page = query.tasks(at=AT)
    assert page['total'] == 1 and page['items'][0]['id'] == first['tasks'][0]['id']
    assert page['items'][0]['deadline'] is None and page['items'][0]['date_state'] == 'undated'

def test_task_projection_orders_dates_without_multiplying_tasks(tmp_path):
    store = new_store(tmp_path/'synthetic.sqlite3')
    event = store.create_collection(CollectionInput(original_text='Synthetic dates',title='Multiple dates'))
    for value in ['2026-11-11','2026-11-09','2026-11-10']:
        event = store.add_deadline(event['id'],DeadlineInput(expected_version=event['version'],label=value,value=value))
    for deadline in event['deadlines']:
        event = store.add_task(event['id'],TaskInput(expected_version=event['version'],title=deadline['label'],deadline_id=deadline['id']))
    event = store.add_task(event['id'],TaskInput(expected_version=event['version'],title='No guessed date'))
    query = PlanningQuery(store)
    all_items = query.tasks(at=AT,status='all',limit=100)['items']
    assert [item['date_state'] for item in all_items] == ['overdue','today','upcoming','undated']
    assert len({item['id'] for item in all_items}) == 4
    first,second = query.tasks(at=AT,limit=2), query.tasks(at=AT,limit=2,offset=2)
    assert first['total'] == second['total'] == 4
    assert {item['id'] for item in first['items']}.isdisjoint(item['id'] for item in second['items'])
    task = event['tasks'][0]
    event = store.edit_task(event['id'],task['id'],event['version'],{'status':'done'})
    assert query.tasks(at=AT,status='todo')['total'] == 3
    assert query.tasks(at=AT,status='done')['total'] == 1
    store.change_status(event['id'],event['version'],'cancelled',True)
    assert query.tasks(at=AT,status='all')['total'] == 0

def test_unknown_and_unconfirmed_dates_do_not_become_scheduled_task_dates(tmp_path):
    store = new_store(tmp_path/'synthetic.sqlite3')
    event = store.create_collection(CollectionInput(original_text='Synthetic unknown',title='Unknown date'))
    event = store.add_deadline(event['id'],DeadlineInput(expected_version=event['version'],label='Pending',precision='unknown',confirmed_by_user=False))
    event = store.add_task(event['id'],TaskInput(expected_version=event['version'],title='Still actionable',deadline_id=event['deadlines'][0]['id']))
    task = PlanningQuery(store).tasks(at=AT)['items'][0]
    assert task['date_state'] == 'needs_confirmation'
    assert PlanningQuery(store).today(at=AT)['total'] == 0
