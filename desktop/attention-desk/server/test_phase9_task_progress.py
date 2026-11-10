from uuid import uuid4
import pytest
from server.personal_store import PersonalStore,PersonalError
from server.personal_models import CollectionInput,TaskInput
from server.cloud_sync.progress import apply_task_progress,rebase_task_versions
from server.cloud_sync.commands import CommandExecutor
from server.cloud_sync.settings import WorkerSettings


@pytest.fixture
def data(tmp_path):
    personal=PersonalStore(tmp_path/'synthetic.sqlite3');personal.initialize()
    event=personal.create_collection(CollectionInput(original_text='Synthetic task original',title='Synthetic event'))
    event=personal.add_task(event['id'],TaskInput(expected_version=event['version'],title='Read the synthetic note'))
    task=event['tasks'][0]
    value={'id':task['id'],'revision':100,'payload':{'event_id':event['id'],'source_title':task['title'],'source_status':task['status'],'completed':True}}
    return personal,event,task,value


def test_task_completion_and_its_receipt_survive_lost_ack_without_duplicate_audit(data):
    personal,event,task,row=data
    apply_task_progress(personal,row);first=personal.get_event(event['id'])
    apply_task_progress(personal,row)
    assert personal.get_event(event['id'])==first and first['tasks'][0]['status']=='done'
    assert first['tasks'][0]['id']==task['id'] and first['version']==event['version']+1
    with personal.connection() as c:assert c.execute('SELECT count(*) FROM cloud_task_application').fetchone()[0]==1


def test_delayed_cloud_toggle_and_parent_completion_rebase_only_our_own_applications(data):
    personal,event,task,row=data
    apply_task_progress(personal,row)
    # Another device reversed the completion before receiving the Windows mirror.
    row={**row,'revision':101,'payload':{**row['payload'],'completed':False}}
    apply_task_progress(personal,row)
    row={**row,'revision':102,'payload':{**row['payload'],'completed':True}}
    apply_task_progress(personal,row)
    result=CommandExecutor(personal,None,None,None).pure({'id':str(uuid4()),'method':'POST','path':'/events/'+event['id']+'/status',
        'body':{'expected_version':event['version'],'status':'done','confirm_incomplete':False}})
    assert result['status']=='done'


def test_independent_edits_are_not_silently_rebased_or_overwritten(data):
    personal,event,task,row=data
    personal.edit_task(event['id'],task['id'],event['version'],{'title':'Changed independently'})
    with pytest.raises(PersonalError,match='另一处'):apply_task_progress(personal,row)
    assert personal.get_event(event['id'])['tasks'][0]['title']=='Changed independently'
    with personal.connection() as c:assert rebase_task_versions(c,event['id'],event['version'],event['version']+1) is None


def test_closed_parent_requires_explicit_restoration_before_task_changes(data):
    personal,event,task,row=data
    personal.change_status(event['id'],event['version'],'done',confirm_incomplete=True)
    with pytest.raises(PersonalError,match='已结束'):apply_task_progress(personal,row)
    assert personal.get_event(event['id'])['tasks'][0]['status']=='todo'


def test_worker_identity_is_separate_encrypted_stable_and_not_enabled_before_registration(tmp_path):
    settings=WorkerSettings(tmp_path/'worker.json',protector=lambda value,decrypt=False:value.removeprefix('protected:') if decrypt else 'protected:'+value)
    workspace,installation=str(uuid4()),str(uuid4())
    first=settings.prepare(workspace,installation)
    assert settings.prepare(workspace,installation)==first
    assert not settings.read()['enabled'] and settings.read()['worker_key'].startswith('ework_')
    assert 'worker_key' not in first and set(first)=={'id','workspace_id','label','key_hash'}
    assert 'protected_worker_key' in settings.path.read_text()
    settings.enable();assert settings.read()['enabled']
    with pytest.raises(PersonalError):settings.prepare(str(uuid4()),installation)
