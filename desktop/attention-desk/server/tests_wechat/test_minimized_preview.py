"""Selected-text review cannot export another message, attachment or identity."""
import asyncio
import json

import pytest

from server.cloud_sync.minimization import selected_text_preview,redact_text
from server.cloud_sync.minimized_projection import minimized_projection_preview
from server.personal_store import encode
import hashlib
from server.tests_wechat.test_pipeline import setup_service,scan
from server.wechat_collect.models import Observation,ObservedMessage
from server.courses import CourseStore


def sample(personal):
    excerpt='请于11月18日周三晚修时间参加考试，不强制统一上交手机。'
    original='联系人李老师，13800138000，student@example.test。'+excerpt
    messages=(ObservedMessage(original,sender='李老师',source_time='2026-10-29T09:42:46+08:00',time_reliability='reliable'),
        ObservedMessage('其他闲聊：个人资料与无关的家庭信息。',sender='其他同学'))
    service,_,calls=setup_service(personal,[Observation('test','unverified',messages,True,True)])
    service.document.wechat.context_followup.enabled=False
    asyncio.run(scan(service))
    with personal.connection() as c:
        batch=c.execute('SELECT * FROM wechat_batch LIMIT 1').fetchone()
        message=c.execute('SELECT id FROM wechat_message WHERE raw_text=?',(original,)).fetchone()[0]
    selection={'raw_item_id':batch['raw_item_id'],'snapshot_id':batch['snapshot_id'],'title':'考试安排',
        'reason':'必要学校事务','schedule_quotes':['11月18日周三晚修时间'],
        'messages':[{'id':message,'excerpts':[excerpt]}]}
    return selection,calls


def test_only_selected_evidence_enters_preview_without_any_domain_change(personal):
    selection,calls=sample(personal)
    with personal.connection() as c:
        before={t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in ('raw_item','raw_snapshot','wechat_message','wechat_llm_attempt','event','task')}
    first=selected_text_preview(personal,[selection]);second=selected_text_preview(personal,[selection])
    assert first==second and first['planned_record_count']==4 and first['proposed_raw_count']==1
    assert first['upload_authorized'] is False and first['attachment_count']==0
    payload=first['samples'][0]['payload']
    assert payload['source_message_ids']==[selection['messages'][0]['id']]
    assert payload['published_at']==['2026-10-29T09:42:46+08:00']
    assert '不强制统一上交手机' in payload['text']
    encoded=json.dumps(first,ensure_ascii=False)
    for private in ('李老师','13800138000','student@example.test','其他闲聊','家庭信息',str(personal.path.parent)):
        assert private not in encoded
    assert payload['attachments']==[] and payload['course'] is None
    with personal.connection() as c:
        assert before=={t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in before}
    assert calls==[]


def test_existing_course_link_is_preserved_and_not_inferred(personal):
    selection,_=sample(personal)
    assert selected_text_preview(personal,[selection])['samples'][0]['payload']['course'] is None
    with personal.connection(write=True) as c:
        course=CourseStore._create(c,'高等数学 II')
        c.execute('UPDATE raw_item SET inbox_course_id=? WHERE id=?',(course['id'],selection['raw_item_id']))
    payload=selected_text_preview(personal,[selection])['samples'][0]['payload']
    assert payload['course']['id']==course['id']
    assert payload['course_link_basis']=='existing_confirmed_link'


def test_excerpt_and_schedule_must_be_real_selected_source_text(personal):
    selection,_=sample(personal)
    original_excerpts=selection['messages'][0]['excerpts'][:]
    selection['messages'][0]['excerpts']=['虚构的11月19日新截止时间']
    with pytest.raises(ValueError,match='excerpt_not_in_original_message'):
        selected_text_preview(personal,[selection])
    selection['messages'][0]['excerpts']=original_excerpts
    selection['schedule_quotes']=['11月19日']
    with pytest.raises(ValueError,match='schedule_quote_not_in_selected_evidence'):
        selected_text_preview(personal,[selection])


def test_preview_ignores_attempted_upload_authorization_and_files(personal):
    selection,_=sample(personal)
    selection.update(upload_authorized=True,attachments=['private.jpg'],sender='private name')
    result=selected_text_preview(personal,[selection])
    assert not result['upload_authorized'] and not result['samples'][0]['upload_authorized']
    payload=result['samples'][0]['payload']
    assert payload['attachments']==[] and 'sender' not in payload


def test_snapshot_cannot_point_to_an_unrelated_record(personal):
    selection,_=sample(personal)
    selection['snapshot_id']='not-the-original-snapshot'
    with pytest.raises(ValueError,match='snapshot_scope_mismatch'):
        selected_text_preview(personal,[selection])


def test_residual_sensitive_subject_is_held_out_of_proposed_count(personal):
    selection,_=sample(personal)
    selection['title']='医保资料核对'
    result=selected_text_preview(personal,[selection])
    assert result['proposed_raw_count']==result['planned_record_count']==0
    assert result['samples'][0]['review_status']=='sensitive_content_requires_exclusion'


def test_contact_masking_handles_email_before_mentions_and_keeps_dates():
    original='李同学 student@example.test 3921594236@qq.com @其他同学 wxid_private 138-0013-8000 https://example.test/?token=secret 2026年11月18日，不强制报名。'
    safe=redact_text(original,known_names=['李同学'])
    for private in ('李同学','student','3921594236','qq.com','其他同学','wxid_private','138','example.test','secret'):
        assert private not in safe
    assert '2026年11月18日，不强制报名。' in safe


def test_all_four_existing_projections_exclude_hidden_identity_context_and_drafts(personal):
    selection,calls=sample(personal)
    with personal.connection(write=True) as c:
        c.execute('UPDATE raw_review SET draft_json=? WHERE raw_snapshot_id=?',
            (json.dumps({'private':'hidden-draft-contact@example.test'}),selection['snapshot_id']))
        c.execute('UPDATE raw_item SET inbox_classification_json=?,inbox_error_json=? WHERE id=?',
            (json.dumps({'private':'hidden-classification'}),json.dumps({'message':'hidden-error'}),selection['raw_item_id']))
    preview=selected_text_preview(personal,[selection])
    first=minimized_projection_preview(personal,preview)
    second=minimized_projection_preview(personal,preview)
    assert first==second and first['record_count']==4
    assert {r['kind'] for r in first['records']}=={'raw_item','inbox_item','inbox','raw_review'}
    assert first['resources']==[] and first['upload_authorized'] is False
    encoded=json.dumps(first,ensure_ascii=False)
    for private in ('李老师','13800138000','student@example.test','其他闲聊','家庭信息',
            'hidden-draft','hidden-classification','hidden-error','database_identity',
            'local_excerpt_spans','local_evidence_sha256','raw_sender_id','chat_name','sender'):
        assert private not in encoded
    for record in first['records']:
        assert record['payload'].get('title',record['payload'].get('title_hint','')).startswith('【脱敏节选】')
    detail=next(r['payload'] for r in first['records'] if r['kind']=='inbox')
    assert detail['attachments']==detail['school_documents']==detail['receipts']==[]
    assert detail['original']==detail['review']['snapshot']
    assert detail['original']['original_text']==preview['samples'][0]['payload']['text']
    assert all(m['raw_text']==preview['samples'][0]['payload']['text'] for m in detail['original']['wechat']['messages'])
    with personal.connection() as c:
        captured=c.execute('SELECT captured_at FROM wechat_message WHERE id=?',(selection['messages'][0]['id'],)).fetchone()[0]
    assert all(m['captured_at']==captured for m in detail['original']['wechat']['messages'])
    assert detail['review']['draft'] is detail['review']['extraction'] is None
    assert calls==[]


@pytest.mark.parametrize('rehash',[False,True])
def test_payload_tampering_cannot_inject_unselected_text_even_with_a_new_hash(personal,rehash):
    selection,_=sample(personal)
    preview=selected_text_preview(personal,[selection])
    preview['samples'][0]['payload']['text']+=' unselected-private-content'
    if rehash:
        preview['samples'][0]['preview_hash']=hashlib.sha256(encode(preview['samples'][0]['payload']).encode()).hexdigest()
    with pytest.raises(ValueError,match='review_content_changed'):
        minimized_projection_preview(personal,preview)


def test_changed_original_or_duplicate_raw_invalidates_the_review_bundle(personal):
    selection,_=sample(personal)
    preview=selected_text_preview(personal,[selection,selection])
    with pytest.raises(ValueError,match='duplicate_raw_selection'):
        minimized_projection_preview(personal,preview)
    preview=selected_text_preview(personal,[selection])
    preview['samples'][0]['local_evidence'][0]['local_evidence_sha256']='0'*64
    with pytest.raises(ValueError,match='original_evidence_changed'):
        minimized_projection_preview(personal,preview)


def test_preview_cannot_be_relabelled_as_upload_authorized(personal):
    selection,_=sample(personal)
    preview=selected_text_preview(personal,[selection])
    preview['upload_authorized']=True
    with pytest.raises(ValueError,match='not_an_unapproved_preview'):
        minimized_projection_preview(personal,preview)
