"""Status projection reads existing records; it cannot enqueue or rewrite AI work."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from server.learning.note_projection import note_processing
from server.learning.auto_notes import AutoNotes


@pytest.fixture
def database(tmp_path):
    connection = sqlite3.connect(tmp_path / 'projection.sqlite3')
    connection.row_factory = sqlite3.Row
    connection.executescript('''
      CREATE TABLE learning_capture (id TEXT,raw_item_id TEXT,snapshot_id TEXT,
        created_at TEXT,status TEXT,context_json TEXT,parsed_json TEXT,error_json TEXT);
      CREATE TABLE review_artifact (id TEXT,lecture_id TEXT,status TEXT,content_json TEXT,error_json TEXT);
      CREATE TABLE attachment_job (id TEXT,status TEXT,error_json TEXT);
    ''')
    yield connection
    connection.close()


def capture(connection, state, **extra):
    work = {'status': state, **extra}
    connection.execute('INSERT INTO learning_capture VALUES (?,?,?,?,?,?,NULL,NULL)',
        ('capture', 'raw', 'snapshot', '2026-11-11', 'needs_context', json.dumps({'note_automation': work})))


def project(connection):
    before = connection.total_changes
    result = note_processing(connection, {'id': 'raw', 'kind': 'inbox_lecture_note'}, 'snapshot')
    assert connection.total_changes == before
    return result


@pytest.mark.parametrize('state,expected', [
    ('needs_context', 'needs_context'), ('failed', 'failed'), ('interrupted', 'failed'),
    ('needs_source', 'needs_review'), ('needs_vision_permission', 'needs_review'),
    ('needs_review', 'needs_review'), ('unknown', 'needs_review'),
])
def test_real_reason_is_not_a_waiting_placeholder(database, state, expected):
    capture(database, state, message='Existing reason')
    result = project(database)
    assert result['status'] == expected and result['message'] == 'Existing reason'
    assert result['summary'] is None and result['review_id'] is None


def test_completed_reuses_saved_result_even_while_paused(database, tmp_path):
    (tmp_path / 'recovery-controls.json').write_text(json.dumps({
        'version': 1, 'hold_cloud_uploads': True, 'pause_paid_ai': True}))
    capture(database, 'completed', review_id='review', lecture_id='lecture')
    database.execute('INSERT INTO review_artifact VALUES (?,?,?,?,NULL)',
        ('review', 'lecture', 'needs_review', json.dumps({'notes_taught': [{'text': 'Saved summary'}]})))
    before = list(database.iterdump())
    assert project(database) == {
        'capture_id': 'capture', 'snapshot_id': 'snapshot', 'status': 'completed',
        'message': '笔记分析已完成，复盘待核对。', 'summary': 'Saved summary', 'review_id': 'review'}
    assert list(database.iterdump()) == before


@pytest.mark.parametrize('lecture,status,content', [
    ('wrong', 'needs_review', {'notes_taught': []}), ('lecture', 'failed', {'notes_taught': []}),
    ('lecture', 'needs_review', None), ('lecture', 'rejected', {'notes_taught': []}),
])
def test_unavailable_or_unrelated_review_never_claims_completed(database, lecture, status, content):
    capture(database, 'completed', review_id='review', lecture_id='lecture')
    database.execute('INSERT INTO review_artifact VALUES (?,?,?,?,NULL)', ('review', lecture, status, json.dumps(content)))
    assert project(database)['status'] == 'needs_review'


def test_stale_snapshot_is_not_exposed(database):
    capture(database, 'completed', review_id='review', lecture_id='lecture')
    database.execute("UPDATE learning_capture SET snapshot_id='old-snapshot'")
    assert project(database) is None


def test_paused_maps_without_changing_stored_state(database, tmp_path):
    path = tmp_path / 'recovery-controls.json'
    path.write_text(json.dumps({'version': 1, 'hold_cloud_uploads': True, 'pause_paid_ai': True}))
    capture(database, 'local_ready')
    assert project(database)['status'] == 'paused'
    assert json.loads(database.execute('SELECT context_json FROM learning_capture').fetchone()[0])['note_automation']['status'] == 'local_ready'
    path.write_text(json.dumps({'version': 1, 'hold_cloud_uploads': True, 'pause_paid_ai': False}))
    assert project(database)['status'] == 'needs_review'


def test_publisher_snapshot_uses_source_controls(database, tmp_path, monkeypatch):
    monkeypatch.delenv('ATTENTION_RECOVERY_CONTROL_PATH')
    source=tmp_path/'source';source.mkdir()
    (source/'recovery-controls.json').write_text(json.dumps({
        'version':1,'hold_cloud_uploads':True,'pause_paid_ai':True}))
    capture(database,'local_ready')
    assert project(database)['status']=='needs_review'
    value=note_processing(database,{'id':'raw','kind':'inbox_lecture_note'},'snapshot',
                          recovery_database_path=source/'personal.sqlite3')
    assert value['status']=='paused'


def test_saved_error_never_claims_success(database):
    capture(database,'completed',review_id='review',lecture_id='lecture')
    database.execute('INSERT INTO review_artifact VALUES (?,?,?,?,?)',
        ('review','lecture','needs_review',json.dumps({'notes_taught':[{'text':'Partial result'}]}),'{"code":"invalid_output"}'))
    assert project(database)['status']=='needs_review'


@pytest.mark.parametrize('job_state,expected',[('running','processing'),('failed','failed'),('completed','needs_review')])
def test_active_label_requires_a_real_active_job(database,job_state,expected):
    capture(database,'processing',review_job_id='job')
    database.execute('INSERT INTO attachment_job VALUES (?,?,?)',('job',job_state,'{"message":"Recorded failure"}'))
    assert project(database)['status']==expected


def test_auto_review_stops_before_model_reservation_and_keeps_resumable_state(tmp_path):
    path = tmp_path / 'recovery-controls.json'
    path.write_text(json.dumps({'version': 1, 'hold_cloud_uploads': True, 'pause_paid_ai': True}))
    before = path.read_bytes()
    # No school methods or model API exist: accessing either would fail.
    service = SimpleNamespace(personal=SimpleNamespace(path=tmp_path / 'isolated.sqlite3'), school=None)
    context = {'lecture_id': 'lecture', 'note_automation': {'status': 'local_ready'}}
    asyncio.run(AutoNotes(service).review(context))
    assert context['note_automation']['status'] == 'needs_setup'
    assert '暂停' in context['note_automation']['message']
    assert path.read_bytes() == before
