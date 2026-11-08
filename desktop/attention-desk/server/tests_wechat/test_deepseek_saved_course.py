"""Replay a real DeepSeek response to a fictional input through an isolated queue."""
import asyncio
import json
from pathlib import Path

from server.courses import CourseStore
from server.review_store import ReviewStore
from server.tests_wechat.test_pipeline import setup_service, scan
from server.tests_wechat.conftest import personal
from server.wechat_collect.models import Observation, ObservedMessage


def test_saved_deepseek_deadline_course_suggestion_and_queue_dedupe(personal):
    fixture=json.loads((Path(__file__).resolve().parents[1]/'fixtures/wechat/deepseek-h01-response.json').read_text(encoding='utf8'))
    text='同学们，请在下周二中午12点前完成高等数学作业。'
    def response(payload):
        aliases={'h01':payload['batch_id'],'m1':payload['messages'][0]['message_id'],
                 'b1':payload['blocks'][0]['block_id']}
        def remap(value):
            if isinstance(value,dict):return {k:remap(v) for k,v in value.items()}
            if isinstance(value,list):return [remap(v) for v in value]
            return aliases.get(value,value) if isinstance(value,str) else value
        return remap(fixture)
    observed=ObservedMessage(text,source_time='2026-11-08T10:00:00+08:00',time_reliability='reliable')
    service,_,calls=setup_service(personal,[Observation('test','unverified',(observed,),True,True)],
        time='2026-11-08T10:00:00+08:00',response=response)
    async def run():
        await scan(service)
        await service.process_queue()
        await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1
    with personal.connection() as c:
        sid=c.execute('SELECT snapshot_id FROM wechat_batch').fetchone()[0]
        assert c.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0
    bundle=ReviewStore(personal).get(sid)
    event=bundle['extraction']['parsed_output']['events'][0]
    assert event['deadlines'][0]['normalized_date']=='2026-11-10T12:00'
    assert bundle['course_suggestions'][event['id']]['id']=='course-math-ii'
    assert event['actions']


def test_existing_course_matching_rejects_other_term_and_ambiguous_subject(personal):
    CourseStore(personal).create('线性代数')
    with personal.connection() as c:
        assert CourseStore.suggest(c,'请提交高等数学 I 作业') is None
        assert CourseStore.suggest(c,'请提交高等数学和线性代数作业') is None
        assert CourseStore.suggest(c,'明天请提交课程作业') is None
