"""New automation gates and transactions; all data and calls are synthetic."""
import asyncio
from datetime import timedelta
import json

import pytest

from server.personal_store import encode
from server.user_context import UserContext,UserContextStore
from server.tests_wechat.test_queue_starvation_regression import rig
from server.wechat_collect.automation import FILENAME,POLICY,activation,eligibility
from server.wechat_collect.deepseek_content_gate import binding_digest
from server.cloud_sync.automatic_actions import build_automatic_preview
from server.cloud_sync.wechat_incremental_journal import freeze_bundle


def enable(rig, *, create=True, source_kind='class'):
    # Normal enabled operation is simulated only in this temporary directory.
    (rig.personal.path.parent/'recovery-controls.json').write_text(encode({
        'version':1,'pause_paid_ai':False,'hold_cloud_uploads':False}),'utf-8')
    context=UserContext(class_name='2026级车辆工程2班',entry_year=2026,
        confirmed_fields=['class_name','entry_year'])
    store=UserContextStore(rig.personal.path.parent); context=store.save(context,store.read().profile_version)
    with rig.personal.connection() as c:
        chat=c.execute('SELECT * FROM wechat_chat WHERE id=?',('test',)).fetchone()
        floor=c.execute('SELECT coalesce(max(ingest_seq),0) FROM wechat_message').fetchone()[0]
    v={'version':2,'policy':POLICY,'enabled':True,'authorization_record':'isolated-user-approval',
        'approved_at':rig.clock[0].isoformat(),'not_before':rig.clock[0].isoformat(),
        'profile_hash':context.fingerprint(),'permissions':{'analyze':True,'create_tasks':create,'publish_tasks':True},
        'http_daily':1000,'tokens_daily':None,
        'sources':{'test':{'after_seq':floor,'binding_hash':binding_digest(chat['source_id'],chat['binding_json']),
                           'audience':{'kind':source_kind,'value':context.class_name if source_kind=='class' else ''}}}}
    (rig.personal.path.parent/FILENAME).write_text(encode(v),'utf-8')
    from server.cloud_sync.notification_runtime import implementation_hash
    release=rig.personal.path.parent/'cloud-sync/notification-automation/release.json'
    release.parent.mkdir(parents=True,exist_ok=True)
    import hashlib
    release.write_text(encode({'implementation_hash':implementation_hash(),
        'proof':{'api_scope':{'activation_hash':hashlib.sha256(encode(v).encode()).hexdigest()}}}),'utf-8')
    rig.worker.automatic_mode=True; rig.worker.queue.automation_directory=rig.personal.path.parent
    rig.worker.queue.exclusive_worker=True  # Synthetic stand-in for main()'s acquired singleton.
    return v


def fresh(rig,text='请全体同学提交实验报告。'):
    rig.clock[0]+=timedelta(minutes=15)
    return rig.add(text)


def counts(rig):
    with rig.personal.connection() as c:
        return {t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['event','task','wechat_llm_attempt']}


def test_new_messages_automatically_analyze_promote_and_resume_two_cycles_without_repeating_old_36(rig):
    old=[rig.add('请全体同学提交旧报告'+str(i)+'。') for i in range(36)]
    for batch in old[:4]: rig.worker.queue.enqueue(batch['id'],rig.service.context.read())
    with rig.personal.connection() as c:
        original_jobs=[tuple(r) for r in c.execute('SELECT * FROM wechat_analysis_job ORDER BY id')]
        original_messages=[tuple(r) for r in c.execute('SELECT * FROM wechat_message ORDER BY id')]
    enable(rig)
    for i in range(3):
        fresh(rig,'请全体同学提交新实验报告'+str(i)+'。')
        result=asyncio.run(rig.worker.cycle())
        assert result['calls']==result['parsed']==result['created']==1
        assert asyncio.run(rig.worker.cycle())['calls']==0
    assert counts(rig)=={'event':3,'task':3,'wechat_llm_attempt':3}
    with rig.personal.connection() as c:
        assert [tuple(c.execute('SELECT * FROM wechat_analysis_job WHERE id=?',(r[0],)).fetchone()) for r in original_jobs]==original_jobs
        assert [tuple(c.execute('SELECT * FROM wechat_message WHERE id=?',(r[0],)).fetchone()) for r in original_messages]==original_messages
        assert c.execute("SELECT count(*) FROM user_decision WHERE action='wechat_auto_created'").fetchone()[0]==3
        assert c.execute("SELECT count(*) FROM wechat_analysis_job WHERE status='running'").fetchone()[0]==0
    assert rig.worker.policy.config.budget.http_daily==1000
    assert rig.service.settings.read().wechat.budget.http_daily==30


@pytest.mark.parametrize('change',['missing','revoked','profile_changed','permission'])
def test_automatic_runtime_never_inherits_legacy_analysis_permission(rig,change):
    v=enable(rig); fresh(rig)
    path=rig.personal.path.parent/FILENAME
    if change=='missing': path.unlink()
    elif change=='revoked': v['enabled']=False; path.write_text(encode(v),'utf-8')
    elif change=='permission': v['permissions']['analyze']=False; path.write_text(encode(v),'utf-8')
    else:
        store=UserContextStore(rig.personal.path.parent)
        store.save(store.read().model_copy(update={'class_name':'别的班'}),store.read().profile_version)
    result=asyncio.run(rig.worker.cycle())
    assert result['state']=='automation_approval_required'
    assert counts(rig)=={'event':0,'task':0,'wechat_llm_attempt':0}


@pytest.mark.parametrize('text',[
    '请团支书提交年度评议材料。',
    '有意参加的同学请提交报名材料。',
    '请名单内的同学提交实验报告。',
    '请未完成的同学提交实验报告。',
    '请2025级全体同学提交实验报告。',
    '请全体同学提交实验报告，团支书汇总材料。',
    '收到',
])
def test_uncertain_roles_conditions_other_year_and_chat_never_become_personal_tasks(rig,text):
    enable(rig); fresh(rig,text)
    asyncio.run(rig.worker.cycle())
    assert counts(rig)['event']==counts(rig)['task']==0


def test_generic_notice_in_volunteer_group_does_not_prove_personal_membership(rig):
    enable(rig,source_kind='review'); fresh(rig)
    assert asyncio.run(rig.worker.cycle())['created']==0
    assert counts(rig)['event']==0


def test_creation_permission_is_independent_of_analysis(rig):
    enable(rig,create=False); fresh(rig)
    result=asyncio.run(rig.worker.cycle())
    assert result['calls']==1 and counts(rig)['event']==0


def test_committed_receipt_can_rebuild_minimal_formal_sync_after_restart(rig):
    enable(rig); fresh(rig)
    assert asyncio.run(rig.worker.cycle())['created']==1
    from server.wechat_collect.deepseek_content_gate import leased_grant
    with rig.personal.connection() as c:
        job=c.execute("SELECT id FROM wechat_analysis_job WHERE status='parsed'").fetchone()[0]
    with leased_grant(rig.personal) as deepseek:
        with rig.personal.connection() as c:
            first=build_automatic_preview(c,deepseek,job)
        with rig.personal.connection() as c:
            second=build_automatic_preview(c,deepseek,job)
    assert first==second
    (rig.personal.path.parent/'automatic-preview.json').write_text(encode(first),'utf-8')
    meta=freeze_bundle(first['bundle'],'a'*64)
    assert {'event','task'}<={r['kind'] for r in meta['records']}
    assert len(rig.calls)==1
    event=next(r['payload'] for r in first['bundle']['records'] if r['kind']=='event')
    assert event['status']=='needs_action' and len(event['tasks'])==1
    assert 'decisions' not in event and 'attachments' not in json.dumps(event,ensure_ascii=False).replace('"attachments": []','')


def test_event_and_automatic_receipt_rollback_together_without_another_ai_call(rig,monkeypatch):
    enable(rig); fresh(rig)
    original=rig.worker.personal._audit
    def failed(*a,**k): raise OSError('isolated commit failure')
    monkeypatch.setattr(rig.worker.personal,'_audit',failed)
    with pytest.raises(OSError): asyncio.run(rig.worker.cycle())
    assert counts(rig)=={'event':0,'task':0,'wechat_llm_attempt':1}
    monkeypatch.setattr(rig.worker.personal,'_audit',original)
    result=asyncio.run(rig.worker.cycle())
    assert result['created']==1 and result['calls']==0
    assert counts(rig)=={'event':1,'task':1,'wechat_llm_attempt':1}


def test_action_invitation_is_analyzed_but_does_not_imply_user_intent():
    from server.wechat_collect.filtering import classify
    decision=classify({'raw_text':'还有没有同学想要争取入团积极分子，今天之内得联系我','message_type':'text'},priority='high')
    assert decision.level=='force_analyze'
