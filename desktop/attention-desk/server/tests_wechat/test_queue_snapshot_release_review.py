"""Release-review regression: attachment-only refresh must not reanalyse."""
import asyncio
from datetime import datetime
import json

import httpx
import pytest

from server.ai_transport import DeepSeekTransport
from server.personal_store import PersonalError, encode
from server.tests_wechat.test_deepseek_cloud_preview import analyzed
from server.tests_wechat.test_deepseek_snapshot_refresh import refreshed
from server.tests_wechat.test_pipeline import Settings, output_for
from server.user_context import UserContextStore
from server.wechat_collect.config import ConfigStore
from server.wechat_collect.deepseek_worker import CONTROLS, TextAnalysisWorker
from server.wechat_collect.file_evidence import enrich_existing


def worker_for(personal, calls, monkeypatch):
    directory=personal.path.parent
    config=ConfigStore(directory/'wechat.yaml');document=config.read()
    document.wechat.allowed_chats=['合成测试群']
    config.save(document,document.revision)
    for name,paused in [('recovery-controls.json',True),(CONTROLS,False)]:
        (directory/name).write_text(encode({'version':1,'hold_cloud_uploads':True,'pause_paid_ai':paused}),'utf-8')
    monkeypatch.setenv('ATTENTION_RECOVERY_CONTROL_PATH',str((directory/CONTROLS).resolve()))

    def respond(request):
        calls.append(json.loads(request.content))
        payload=json.loads(calls[-1]['messages'][-1]['content'].split('仅处理以下数据:\n')[1])
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(output_for(payload),ensure_ascii=False)},'finish_reason':'stop'}],
                                      'usage':{'total_tokens':100}})

    return TextAnalysisWorker(directory,clock=lambda:datetime.fromisoformat('2026-10-22T10:00:00+08:00'),
        settings=Settings(),transport=DeepSeekTransport(httpx.MockTransport(respond)),unlimited_test_tokens=True)


@pytest.mark.parametrize('review_status',['pending','confirmed'])
@pytest.mark.parametrize('entrypoint',['cycle','enqueue'])
def test_real_file_evidence_refresh_keeps_completed_job_and_owner_decisions(personal,monkeypatch,review_status,entrypoint):
    calls,job_id=analyzed(personal,media=True)
    with personal.connection(write=True) as c:
        job=dict(c.execute('SELECT * FROM wechat_analysis_job WHERE id=?',(job_id,)).fetchone())
        batch=dict(c.execute('SELECT * FROM wechat_batch WHERE id=?',(job['batch_id'],)).fetchone())
        file_id=c.execute("SELECT m.id FROM wechat_message m JOIN wechat_batch_message bm ON bm.message_id=m.id WHERE bm.batch_id=? AND m.message_type='file'",(batch['id'],)).fetchone()[0]
        c.execute('INSERT INTO wechat_media_work(message_id,reference_json) VALUES (?,?)',
                  (file_id,encode({'kind':'file','title':'合成说明.pdf'})))
        c.execute('UPDATE raw_review SET status=?,draft_json=?,confirmed_event_ids=? WHERE raw_snapshot_id=?',
                  (review_status,encode({'events':[{'id':'notice1','title':'保留人工核对内容'}]}),
                   encode(['preserved-event']) if review_status=='confirmed' else '[]',batch['snapshot_id']))
        if review_status=='confirmed':
            c.execute("UPDATE wechat_candidate SET status='accepted' WHERE batch_id=? AND model_item_id!='_pending'",(batch['id'],))
            c.execute("UPDATE wechat_batch SET status='accepted' WHERE id=?",(batch['id'],))
        original_messages=[tuple(r) for r in c.execute('SELECT * FROM wechat_message ORDER BY id')]
    assert file_id in enrich_existing(personal)
    with personal.connection() as c:
        current=c.execute('SELECT snapshot_id FROM wechat_batch WHERE id=?',(batch['id'],)).fetchone()[0]
        assert current!=job['snapshot_id']
        review=dict(c.execute('SELECT * FROM raw_review WHERE raw_snapshot_id=?',(current,)).fetchone())
        assert review['extraction_id']==job['extraction_id']
        candidate_rows=[tuple(r) for r in c.execute('SELECT * FROM wechat_candidate ORDER BY id')]
    worker=worker_for(personal,calls,monkeypatch)
    if entrypoint=='cycle':
        result=asyncio.run(worker.cycle())
        assert result['enqueued']==result['calls']==0
        assert asyncio.run(worker.cycle())['calls']==0
    else:
        assert worker.queue.enqueue(batch['id'],UserContextStore(personal.path.parent).read())==job_id
    assert len(calls)==1
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==1
        assert dict(c.execute('SELECT * FROM wechat_analysis_job WHERE id=?',(job_id,)).fetchone())==job
        assert dict(c.execute('SELECT * FROM raw_review WHERE raw_snapshot_id=?',(current,)).fetchone())==review
        assert [tuple(r) for r in c.execute('SELECT * FROM wechat_candidate ORDER BY id')]==candidate_rows
        assert [tuple(r) for r in c.execute('SELECT * FROM wechat_message ORDER BY id')]==original_messages


@pytest.mark.parametrize('change',['snapshot_text','block_text','mapping'])
def test_copied_extraction_pointer_does_not_hide_changed_analysis_input(personal,monkeypatch,change):
    calls,job_id=analyzed(personal)
    _,current=refreshed(personal,job_id,change)
    worker=worker_for(personal,calls,monkeypatch)
    with personal.connection() as c:
        job=c.execute('SELECT * FROM wechat_analysis_job WHERE id=?',(job_id,)).fetchone()
        batch=dict(c.execute('SELECT * FROM wechat_batch WHERE id=?',(job['batch_id'],)).fetchone())
        assert worker.queue.completed_job(c,batch) is None
    new_job=worker.queue.enqueue(batch['id'],UserContextStore(personal.path.parent).read())
    assert new_job!=job_id and len(calls)==1
    with personal.connection() as c:
        assert c.execute('SELECT snapshot_id FROM wechat_analysis_job WHERE id=?',(new_job,)).fetchone()[0]==current


@pytest.mark.parametrize('entrypoint',['cycle','enqueue'])
def test_closed_review_without_valid_continuity_cannot_be_rewritten(personal,monkeypatch,entrypoint):
    calls,job_id=analyzed(personal)
    _,current=refreshed(personal,job_id,'snapshot_text')
    with personal.connection(write=True) as c:
        job=c.execute('SELECT * FROM wechat_analysis_job WHERE id=?',(job_id,)).fetchone()
        batch_id=job['batch_id']
        c.execute("UPDATE raw_review SET status='confirmed' WHERE raw_snapshot_id=?",(current,))
        before=dict(c.execute('SELECT * FROM raw_review WHERE raw_snapshot_id=?',(current,)).fetchone())
    worker=worker_for(personal,calls,monkeypatch)
    if entrypoint=='cycle':
        assert asyncio.run(worker.cycle())['enqueued']==0
    else:
        with pytest.raises(PersonalError) as error:
            worker.queue.enqueue(batch_id,UserContextStore(personal.path.parent).read())
        assert error.value.code=='review_closed'
    with personal.connection() as c:
        assert dict(c.execute('SELECT * FROM raw_review WHERE raw_snapshot_id=?',(current,)).fetchone())==before
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==1
    assert len(calls)==1
