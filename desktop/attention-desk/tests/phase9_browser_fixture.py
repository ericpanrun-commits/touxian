"""Isolated Planner acceptance. No main.py, collectors, models or cloud workers."""
import os
from datetime import datetime,timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.responses import FileResponse
from server.personal_store import PersonalStore
from server.personal_models import CollectionInput,DeadlineInput,TaskInput
from server.personal_api import router as personal_router
from server.review_api import router as review_router
from server.review_store import ReviewStore
from server.extraction_models import RawInput
from server.planning.api import router as planning_router
from server.cloud_sync.progress import apply_task_progress

directory=Path(os.environ['ERIC_PHASE9_FIXTURE']).resolve()
if not (directory/'.synthetic-planner-test').is_file():
    raise RuntimeError('Explicit synthetic fixture marker required.')
store=PersonalStore(directory/'synthetic.sqlite3');store.initialize()
app=FastAPI();app.state.personal_store=store
app.include_router(personal_router);app.include_router(review_router);app.include_router(planning_router)
today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
event=store.create_collection(CollectionInput(original_text='Synthetic Planner original',title='隔离测试 · 多日期事项'))
for label,offset in [('合成逾期',-1),('合成今天',0),('合成未来',1)]:
    event=store.add_deadline(event['id'],DeadlineInput(expected_version=event['version'],label=label,value=str(today+timedelta(days=offset))))
for date in event['deadlines']:
    event=store.add_task(event['id'],TaskInput(expected_version=event['version'],title=date['label']+'任务',deadline_id=date['id']))
for i in range(23):
    event=store.add_task(event['id'],TaskInput(expected_version=event['version'],title=f'合成无日期任务 {i:02}'))
seed_event=event['id'];seed_task=event['tasks'][0]['id']

review=ReviewStore(store)
raw=review.collect(RawInput(original_text='合成原文：提交申请；补交材料。',title_hint='隔离测试 · 微信确认'),str(uuid4()))
sid=raw['snapshot']['id']
draft={'acknowledged_warnings':True,'events':[{'id':'synthetic-notice','selected':True,'title':'隔离测试 · 微信确认','summary':'合成通知，仅用于隔离验收。','category':'task','course_id':None,
    'relevance':'yes','participation':'yes','notice_kind':'new','audience':None,'eligibility':[],'materials':[],'locations':[],'uncertainties':[],
    'deadlines':[{'id':f'd{i}','selected':True,'role':label,'value':str(today+timedelta(days=i+1)),'precision':'date','end_value':None,'range_precision':None,'original_text':'','evidence':[],'confirmed_by_user':False,'relation':'current'} for i,label in enumerate(['申请日期','材料日期'])],
    'actions':[{'id':'a1','selected':True,'title':'提交申请','kind':'explicit','deadline_id':'d0','evidence':[{'quote':'提交申请','start_offset':5,'end_offset':9}]},
               {'id':'a2','selected':True,'title':'补交材料','kind':'explicit','deadline_id':'d1','evidence':[{'quote':'补交材料','start_offset':10,'end_offset':14}]}]}]}
review.save_draft(sid,raw['version'],draft)

@app.get('/api/personal/wechat/candidates')
def candidates():
    current=review.get(sid)
    return {'items':[] if current['status']=='confirmed' else [{'id':'synthetic-candidate','snapshot_id':sid,'review_version':current['version'],'chat_name':'合成测试群','messages':[],
        'analysis':{'title':'隔离测试 · 微信确认','summary':'合成通知，仅用于隔离验收。','analysis_origin':'local_pending'},'draft_preview':draft['events'][0]}], 'total':0 if current['status']=='confirmed' else 1}

@app.get('/api/personal/learning')
def learning():
    return {'topics':[],'courses':[],'pending_captures':[],'overview':{},'browser_sync':{},'mistakes':[],'retests':[]}

@app.get('/api/personal/wechat/briefing')
def briefing():
    return {'unresolved':{'items':[],'total':0},'media':{'items':[],'total':0}}

@app.get('/__test__/state')
def state():
    with store.connection() as c:
        counts={name:c.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in ['event','task','deadline','raw_snapshot']}
    return {'synthetic_only':True,'event_id':seed_event,'task_id':seed_task,'snapshot_id':sid,'counts':counts,'today':str(today)}

@app.get('/__test__/cloud')
def cloud():
    with store.connection() as c:
        records=[]
        for name in ['event','task','deadline']:
            for row in c.execute('SELECT * FROM '+name):
                value=store._detail(c,row['id']) if name=='event' else dict(row)
                records.append({'kind':name,'id':row['id'],'payload':value,'revision':100+len(records)})
    from server.planning.query import PlanningQuery
    records.append({'kind':'facade','id':'/planning/facts','payload':{'items':PlanningQuery(store).deadlines(include_terminal=True)},'revision':99})
    return {'records':records}

@app.post('/__test__/progress')
def progress(row:dict):
    apply_task_progress(store,row)
    return state()

@app.post('/__test__/reopen')
def reopen():
    current=store.get_event(seed_event)
    store.edit_task(seed_event,seed_task,current['version'],{'status':'todo'})
    return state()

@app.get('/api/{path:path}')
def inert(path:str):
    return {'items':[],'total':0,'sources':[],'status':'ok','candidates':[],'chats':[]}

dist=Path(__file__).resolve().parents[1]/'dist'
@app.get('/{path:path}')
def static(path:str):
    file=(dist/path).resolve()
    if not file.is_relative_to(dist) or not file.is_file():file=dist/'index.html'
    return FileResponse(file)

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18796,log_level='warning')
