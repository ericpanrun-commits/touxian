import pytest

from server.tests_wechat.test_deepseek_cloud_preview import personal,analyzed,preview
from server.review_store import ReviewStore
from server.wechat_collect.file_evidence import _copy_row
from server.extraction_errors import ExtractionFailure


def refreshed(personal,job,change=None):
    with personal.connection(write=True) as c:
        saved=c.execute('SELECT * FROM wechat_analysis_job WHERE id=?',(job,)).fetchone()
        original=c.execute('SELECT * FROM raw_snapshot WHERE id=?',(saved['snapshot_id'],)).fetchone()
        old=c.execute('SELECT * FROM raw_review WHERE raw_snapshot_id=?',(original['id'],)).fetchone()
        text=original['original_text'] if change!='snapshot_text' else 'Different synthetic notice'
        sid=ReviewStore._new_snapshot(c,original['raw_item_id'],text,'2026-11-09T00:00:00+00:00',original['source_time'],old['title_hint'])
        fields={k:old[k] for k in old.keys() if k not in {'raw_snapshot_id','created_at','updated_at','version'}}
        c.execute('UPDATE raw_review SET '+','.join(k+'=?' for k in fields)+',version=? WHERE raw_snapshot_id=?',
            (*fields.values(),old['version']+1,sid))
        for row in c.execute('SELECT * FROM raw_source_block WHERE raw_snapshot_id=?',(original['id'],)).fetchall():
            changes={'text':'Different synthetic block'} if change=='block_text' else {}
            _copy_row(c,'raw_source_block',row,raw_snapshot_id=sid,**changes)
        for row in c.execute('SELECT * FROM wechat_snapshot_message WHERE snapshot_id=?',(original['id'],)).fetchall():
            _copy_row(c,'wechat_snapshot_message',row,snapshot_id=sid,
                content_revision=row['content_revision']+int(change=='mapping'))
        c.execute('UPDATE wechat_batch SET snapshot_id=? WHERE id=?',(sid,saved['batch_id']))
        if change=='review':c.execute("UPDATE raw_review SET title_hint='Changed owner decision' WHERE raw_snapshot_id=?",(sid,))
        elif change=='status':c.execute("UPDATE raw_review SET status='cancelled' WHERE raw_snapshot_id=?",(sid,))
        elif change=='version':c.execute('UPDATE raw_review SET version=? WHERE raw_snapshot_id=?',(old['version'],sid))
        elif change=='extraction':c.execute('UPDATE raw_review SET extraction_id=NULL WHERE raw_snapshot_id=?',(sid,))
        return original['id'],sid


def test_identical_text_review_refresh_reuses_model_response_and_current_snapshot(personal):
    calls,job=analyzed(personal)
    before=preview(personal,job);original,sid=refreshed(personal,job)
    after=preview(personal,job)
    assert len(calls)==1
    assert before['analysis_receipt_sha256']==after['analysis_receipt_sha256']
    assert before['bundle']['bundle_hash']!=after['bundle']['bundle_hash']
    records={r['kind']:r['payload'] for r in after['bundle']['records']}
    assert records['raw_item']['inbox_snapshot_id']==records['raw_review']['raw_snapshot_id']==sid
    assert records['raw_review']['snapshot']['original_text']==before['samples']['samples'][0]['payload']['text']
    assert records['raw_review']['snapshot']['attachments']==after['bundle']['resources']==[]
    assert all(message['snapshot_id']==sid for message in records['raw_review']['snapshot']['wechat']['messages'])
    assert after['bundle']['local_guards'][0]['snapshot_id']==sid
    with personal.connection() as c:
        saved=c.execute('SELECT snapshot_id FROM wechat_analysis_job WHERE id=?',(job,)).fetchone()[0]
        assert saved==original  # No rewritten history or additional model job.


@pytest.mark.parametrize('change',['snapshot_text','block_text','review','status','version','extraction','mapping'])
def test_refresh_cannot_hide_changed_text_decisions_or_evidence(personal,change):
    calls,job=analyzed(personal);refreshed(personal,job,change)
    with pytest.raises((ValueError,ExtractionFailure)) as failure:
        preview(personal,job)
    assert getattr(failure.value,'code',str(failure.value)) in {'analysis_snapshot_lineage_changed','analysis_snapshot_review_changed',
        'analysis_snapshot_text_changed','analysis_snapshot_mapping_changed','projection_source_mismatch','privacy_review_required'}
    assert len(calls)==1


def test_current_review_change_after_a_valid_preview_invalidates_saved_projection(personal):
    calls,job=analyzed(personal);_,sid=refreshed(personal,job)
    preview(personal,job)
    with personal.connection(write=True) as c:
        c.execute("UPDATE raw_review SET status='cancelled' WHERE raw_snapshot_id=?",(sid,))
    with pytest.raises(ValueError,match='analysis_snapshot_lineage_changed'):preview(personal,job)
    assert len(calls)==1
