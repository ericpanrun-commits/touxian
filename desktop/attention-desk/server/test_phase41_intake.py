"""Isolated batch ingestion: no platform or model calls and no formal database."""
import asyncio,copy,json
from uuid import uuid4
from server.test_school import rig
from server.learning.service import LearningService
from server.learning.browser_ingest import BrowserIntake,digest
from server.learning.browser_batch import BrowserBatch
from server.learning.store import unpack
from server.test_phase41_browser import preview

def packet(rows=None,kind='preview'):
    return {'schema':'readonly-native-batch-v1','native_course_name':'高等数学Ⅰ1','native_class_name':'《高等数学Ⅰ1》4班',
        'requested_cycle_date':'2026-10-28','records':[{'activity_type':kind,'activity_name':'隔离无穷小比较定义测试题','chapter':'1.7.1',
        'pages':[{'text':rows or preview(),'source_url':'https://mooc1.chaoxing.com/result'}]}]}

def test_batch_repeated_read_saves_sources_without_duplicate_attempts_and_keeps_missing_clocks(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            service=LearningService(r.store,r.service,r.extract);reader=BrowserIntake(service,r.docs)
            first=await reader.ingest(packet(),tmp_path)
            second=await reader.ingest(packet(),tmp_path)
            assert first['counts']['preview']=={'total':2,'correct':1,'incorrect':1,'unknown':0}
            assert second['duplicate_attempts_skipped']==2
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==2
                assert all(x[0] is None for x in c.execute('SELECT occurred_at FROM learning_attempt'))
                assert c.execute('SELECT sum(independent) FROM learning_attempt').fetchone()[0]==0
            await service.close()
    asyncio.run(run())

def test_verified_legacy_alias_avoids_reimporting_an_earlier_manual_capture(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            service=LearningService(r.store,r.service,r.extract);reader=BrowserIntake(service,r.docs)
            await reader.ingest(packet(),tmp_path)
            with r.store.connection(write=True) as c:
                cap=unpack(c.execute('SELECT * FROM learning_capture').fetchone(),'context_json')
                key=cap['context'].pop('native_activity_key')
                c.execute('UPDATE learning_capture SET context_json=? WHERE id=?',(json.dumps(cap['context']),cap['id']))
            work=service.store.reserve_artifact('browser_activity_alias',str(uuid4()))
            service.store.finish_artifact(work['id'],{'native_activity_key':key,'legacy_capture_id':cap['id'],'evidence':'isolated source verification'})
            assert (await reader.ingest(packet(),tmp_path))['duplicate_attempts_skipped']==2
            with r.store.connection() as c:assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==2
            await service.close()
    asyncio.run(run())

def test_pending_platform_keys_stay_unknown_and_unconfirmed_independence_is_not_inferred(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            service=LearningService(r.store,r.service,r.extract)
            rows=['已提交，待教师公布正确答案','10-28 11:56','1.','[单选题]','隔离无穷小比较条件题目','我的答案：','A','正确答案：','A']
            result=await BrowserIntake(service,r.docs).ingest(packet(rows,'classroom'),tmp_path)
            assert result['counts']['classroom']=={'total':1,'correct':0,'incorrect':0,'unknown':1}
            with r.store.connection() as c:
                attempt=c.execute('SELECT * FROM learning_attempt').fetchone()
                assert attempt['occurred_at'] is None and not attempt['independent']
            await service.close()
    asyncio.run(run())

def test_batch_status_keeps_error_and_supports_a_new_request_without_model_or_browser(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            service=LearningService(r.store,r.service,r.extract);batch=BrowserBatch(service)
            assert batch.status()['status']=='idle'
            artifact=service.store.reserve_artifact('browser_batch',str(uuid4()))
            service.store.finish_artifact(artifact['id'],{'message':'请打开已登录课程页。'},status='needs_review')
            assert batch.status()['message']=='请打开已登录课程页。'
            await service.close()
    asyncio.run(run())


def test_native_topic_suffix_is_clean_and_keeps_its_meaning():
    from server.learning.browser_ingest import base_title
    assert base_title('函数连续性的定义测试题')=='函数连续性'
    assert base_title('无穷小的比较定义测试题')=='无穷小的比较'


def test_old_pending_results_keep_their_real_activity_year_and_are_refreshed_without_submission_clocks(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            service=LearningService(r.store,r.service,r.extract)
            rows=['已提交，待教师公布正确答案','12-31 11:56','1.','[单选题]','隔离连续性条件题目','我的答案：','A','正确答案：','A']
            value=packet(rows,'classroom');value['requested_cycle_date']='2027-01-01';value['records'][0]['activity_day']='2026-12-31'
            await BrowserIntake(service,r.docs).ingest(value,tmp_path)
            assert BrowserBatch(service).pending_days('2027-01-01')==['2026-12-31']
            assert BrowserBatch(service).pending_days('2027-02-01')==[]
            with r.store.connection() as c:
                ctx=json.loads(c.execute('SELECT context_json FROM learning_capture').fetchone()[0])
                assert ctx['activity_published_at'].startswith('2026-12-31')
                assert c.execute('SELECT occurred_at FROM learning_attempt').fetchone()[0] is None
            await service.close()
    asyncio.run(run())
