import asyncio
import json

import pytest

from server.tests_wechat.conftest import personal
from server.tests_wechat.test_pipeline import setup_service,scan,output_for
from server.tests_wechat.test_deepseek_content_gate import grant,write_grant
from server.wechat_collect.models import ObservedMessage,Observation
from server.wechat_collect.deepseek_content_gate import leased_grant
from server.cloud_sync.deepseek_preview import build_deepseek_preview


def analyzed(personal,*,response=None,text='请全体同学明天提交实验报告。',run=True):
    observed=ObservedMessage(text,sender='老师',source_time='2026-10-22T10:00:00+08:00',time_reliability='reliable')
    service,_,calls=setup_service(personal,[Observation('test','unverified',(observed,),True,True)],response=response)
    service.synthetic_content_grant=False
    service.document.wechat.context_followup.enabled=False
    asyncio.run(scan(service))
    write_grant(personal,grant())
    with personal.connection() as c:
        batch=c.execute('SELECT id FROM wechat_batch').fetchone()[0]
    job=service.analysis.enqueue(batch,service.context.read())
    if run: assert asyncio.run(service.analysis.run_one(job))
    return calls,job


def preview(personal,job):
    with leased_grant(personal) as allowed:
        with personal.connection() as c:
            c.execute('PRAGMA query_only=ON')
            return build_deepseek_preview(c,allowed,job)


def test_preview_reuses_success_receipt_and_existing_four_projections_without_permission(personal):
    calls,job=analyzed(personal)
    before=len(calls)
    first=preview(personal,job)
    assert first==preview(personal,job)
    assert len(calls)==before==1
    assert first['preview_only'] is True and first['upload_authorized'] is False
    assert first['requires_cloud_outbound_validation'] and first['requires_separate_upload_authorization']
    assert first['notice_count']==first['mandatory_action_count']==1
    assert first['bundle']['record_count']==4 and first['bundle']['resources']==[]
    assert {r['kind'] for r in first['bundle']['records']}=={'raw_item','raw_review','inbox_item','inbox'}


def test_optional_school_notice_is_not_misrepresented_as_mandatory_or_locally_reopened(personal):
    def optional(payload):
        result=output_for(payload)
        result['items'][0].update(category='registration',participation='optional',
            important=False,action_required=False,actions=[],attention_level='fyi')
        return result
    _,job=analyzed(personal,response=optional,text='学校志愿活动接受自愿报名。')
    result=preview(personal,job)
    assert result['notice_count']==1 and result['mandatory_action_count']==0
    review=next(r['payload'] for r in result['bundle']['records'] if r['kind']=='raw_review')
    assert review['status']=='archived'
    with personal.connection() as c:
        assert c.execute('SELECT status FROM raw_review').fetchone()[0]=='archived'
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0


def test_cloud_preview_preserves_exact_contact_removal_and_evidence_positions(personal):
    _,job=analyzed(personal,text='联系人：虚构甲 13800000000\n请全体同学明天提交实验报告。')
    result=preview(personal,job)
    assert '13800000000' not in json.dumps(result,ensure_ascii=False)
    evidence=result['samples']['samples'][0]['local_evidence'][0]
    assert evidence['local_excerpt_spans'][0]['start']>0


@pytest.mark.parametrize('change',['pending','dismissed','candidate_changed','not_notice'])
def test_missing_or_changed_analysis_cannot_become_upload_candidate(personal,change):
    def not_notice(payload):
        result=output_for(payload)
        result['items'][0].update(is_school_notice=False,relevant=False,action_required=False,actions=[],attention_level='ignore')
        return result
    calls,job=analyzed(personal,run=change!='pending',response=not_notice if change=='not_notice' else None)
    if change in {'dismissed','candidate_changed'}:
        with personal.connection(write=True) as c:
            if change=='dismissed':
                c.execute("UPDATE wechat_candidate SET status='ignored' WHERE model_item_id!='_pending'")
            else:
                row=c.execute("SELECT id,data_json FROM wechat_candidate WHERE model_item_id!='_pending'").fetchone()
                data=json.loads(row['data_json']);data['title']='本地被修改的模型结论'
                c.execute('UPDATE wechat_candidate SET data_json=? WHERE id=?',(json.dumps(data),row['id']))
    with pytest.raises(ValueError): preview(personal,job)
    assert len(calls)==(0 if change=='pending' else 1)
