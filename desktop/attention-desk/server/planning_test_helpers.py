"""Anonymous test data shared by Phase 6 acceptance tests."""
from datetime import datetime,timezone
from .personal_models import CollectionInput,DeadlineInput,TaskInput
from .personal_store import PersonalStore

AT = datetime(2026,10,9,1,tzinfo=timezone.utc)


def new_store(path):
    store = PersonalStore(path)
    store.initialize()
    return store


def event_with_deadline(store,value='2026-10-12',precision='date',*,tasks=0,**changes):
    event = store.create_collection(CollectionInput(title='匿名测试事项',original_text='请于10月12日前提交材料。'))
    event = store.add_deadline(event['id'],DeadlineInput(expected_version=event['version'],label='材料提交',
        value=value,precision=precision,timezone='Asia/Shanghai',**changes))
    deadline_id = event['deadlines'][0]['id']
    for index in range(tasks):
        event = store.add_task(event['id'],TaskInput(expected_version=event['version'],title=f'任务 {index+1}',deadline_id=deadline_id))
    return event


class Notifier:
    def __init__(self,result='os_accepted'):
        self.calls = []
        self.result = result

    def send(self,text,**kwargs):
        self.calls.append((text,kwargs))
        return self.result

    def test(self):
        return self.result
