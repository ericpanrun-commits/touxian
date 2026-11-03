"""Real isolated PostgreSQL tests. Never use a production DSN or learner data."""
import hashlib
import os
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import time

import pytest

psycopg = pytest.importorskip('psycopg')
from psycopg import sql
from psycopg.types.json import Jsonb


@pytest.fixture(scope='module')
def cluster():
    dsn = os.environ.get('ERIC_APP_TEST_DSN')
    if not dsn:
        pytest.skip('Isolated PostgreSQL is not configured')
    options = psycopg.conninfo.conninfo_to_dict(dsn)
    options['connect_timeout']='5'
    assert options.get('host') == '127.0.0.1' and options.get('port') == '65438'
    assert options.get('dbname') == 'postgres', 'Tests cannot run against an arbitrary database'
    name = 'eric_v1_test_' + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    options['dbname'] = name
    try:
        with psycopg.connect(**options, autocommit=True) as c:
            root = Path(__file__).resolve().parents[1]
            c.execute('CREATE SCHEMA storage')
            c.execute('CREATE TABLE storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint,allowed_mime_types text[])')
            c.execute('CREATE TABLE storage.objects(id uuid primary key default gen_random_uuid(),bucket_id text,name text,metadata jsonb,unique(bucket_id,name))')
            c.execute('GRANT USAGE ON SCHEMA public,storage TO service_role')
            c.execute('GRANT SELECT ON storage.objects TO service_role')
            for migration in sorted((root/'supabase/migrations').glob('*_eric_app_*.sql')):
                c.execute(migration.read_text(encoding='utf-8'))
        yield options
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


@pytest.fixture
def db(cluster):
    with psycopg.connect(**cluster, autocommit=True) as c:
        owner = uuid4()
        workspace = c.execute('INSERT INTO eric_app_workspaces(owner_id) VALUES(%s) RETURNING id', (owner,)).fetchone()[0]
        device = c.execute("INSERT INTO eric_app_devices(workspace_id,user_id,session_id,label,platform) VALUES(%s,%s,%s,'Synthetic test','web') RETURNING id", (workspace,owner,uuid4())).fetchone()[0]
        yield c, workspace, device


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def test_cloud_task_completion_is_idempotent_fenced_and_independent_of_worker_availability(db):
    c,workspace,device=db
    publish(c,workspace,'event','event-task',{'id':'event-task','status':'active','version':1})
    source=publish(c,workspace,'task','task-original',{'id':'task-original','event_id':'event-task','title':'Original task','status':'todo'})
    request=uuid4();args=(workspace,device,'task-original',0,source,request,digest('complete task'),True)
    first=c.execute('SELECT eric_app_task_progress(%s,%s,%s,%s,%s,%s,%s,%s)',args).fetchone()[0]
    assert first['payload']['completed'] and first['payload']['application_state']=='pending'
    assert c.execute('SELECT eric_app_task_progress(%s,%s,%s,%s,%s,%s,%s,%s)',args).fetchone()[0]==first
    second=c.execute('SELECT eric_app_task_progress(%s,%s,%s,%s,%s,%s,%s,%s)',
        (workspace,device,'task-original',first['revision'],source,uuid4(),digest('undo task'),False)).fetchone()[0]
    assert not c.execute("SELECT eric_app_ack_task(%s,'task-original',%s,'applied','',NULL,0)",(workspace,first['revision'])).fetchone()[0]
    assert not second['payload']['completed']
    with pytest.raises(psycopg.errors.RaiseException,match='version_conflict'):
        c.execute('SELECT eric_app_task_progress(%s,%s,%s,%s,%s,%s,%s,%s)',
            (workspace,device,'task-original',first['revision'],source,uuid4(),digest('stale'),True))
    with pytest.raises(psycopg.errors.RaiseException,match='reserved_record_kind'):
        publish(c,workspace,'task_progress','task-original',{'completed':True})
    c.execute('SELECT eric_app_revoke_device(%s,%s)',(workspace,device))
    with pytest.raises(psycopg.errors.RaiseException,match='device_revoked'):
        c.execute('SELECT eric_app_task_progress(%s,%s,%s,%s,%s,%s,%s,%s)',
            (workspace,device,'task-original',second['revision'],source,uuid4(),digest('revoked'),True))


def publish(c, workspace, kind='review', record_id='review-fixture', payload=None, epoch=None, generation=0):
    data = payload or {'id': record_id, 'version': 1, 'content': {'summary': 'Synthetic review'}}
    row = {'kind': kind, 'id': record_id, 'payload': data, 'source_hash': digest(repr(data))}
    return c.execute('SELECT eric_app_publish_records(%s,%s,%s,%s)', (workspace,Jsonb([row]),epoch,generation)).fetchone()[0]


def capture(c, workspace, device, *, capture_id=None, files=None, request_hash=None):
    row = {'id': str(capture_id or uuid4()), 'device_id': str(device), 'source': 'pwa',
           'request_hash': request_hash or digest('synthetic capture'), 'title': 'Synthetic capture',
           'purpose': 'lecture_note', 'files': files or []}
    result = c.execute('SELECT id,state FROM eric_app_reserve_capture(%s,%s)', (workspace,Jsonb(row))).fetchone()
    return row, result


def test_deny_all_direct_clients_and_prevent_physical_record_delete(db):
    c, workspace, _ = db
    publish(c, workspace)
    for role in ('anon', 'authenticated'):
        c.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute('SELECT * FROM eric_app_records')
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute('SELECT eric_app_claim_work(%s,%s,3)', (workspace,'captures'))
        finally:
            c.execute('RESET ROLE')
    c.execute('SET ROLE service_role')
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute('DELETE FROM eric_app_records WHERE workspace_id=%s', (workspace,))
    finally:
        c.execute('RESET ROLE')


def test_text_ids_are_preserved_and_identical_publish_does_not_create_a_change(db):
    c, workspace, _ = db
    first = publish(c,workspace,kind='course',record_id='course-math-ii')
    second = publish(c,workspace,kind='course',record_id='course-math-ii')
    assert first == second
    assert c.execute('SELECT id FROM eric_app_records WHERE workspace_id=%s', (workspace,)).fetchone()[0] == 'course-math-ii'


def test_devices_resume_same_session_and_revoked_session_cannot_reenroll(db):
    c, workspace, device = db
    owner, session = c.execute('SELECT user_id,session_id FROM eric_app_devices WHERE id=%s', (device,)).fetchone()
    resumed = c.execute("SELECT id FROM eric_app_device_session(%s,%s,%s,'Synthetic phone','iphone')", (workspace,owner,session)).fetchone()[0]
    assert resumed == device
    assert c.execute('SELECT eric_app_revoke_device(%s,%s)', (workspace,device)).fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException, match='eric_device_revoked'):
        c.execute("SELECT id FROM eric_app_device_session(%s,%s,%s,'Synthetic phone','iphone')", (workspace,owner,session))


def test_change_cursor_cannot_skip_a_transaction_committing_out_of_order(db,cluster):
    c, workspace, _ = db
    name = 'synthetic_cursor_' + uuid4().hex
    with psycopg.connect(**cluster, autocommit=False) as first:
        publish(first,workspace,'course','first',{'name':'First'})
        def second():
            with psycopg.connect(**cluster,autocommit=False,application_name=name) as connection:
                publish(connection,workspace,'course','second',{'name':'Second'})
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(second)
            try:
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    row=c.execute('SELECT wait_event FROM pg_stat_activity WHERE application_name=%s',(name,)).fetchone()
                    if row and row[0]=='advisory': break
                    time.sleep(.02)
                else: pytest.fail('Second writer did not reach the actual transaction lock')
                assert c.execute('SELECT count(*) FROM eric_app_changes WHERE workspace_id=%s',(workspace,)).fetchone()[0]==0
            finally:
                first.commit()
            future.result(timeout=3)
    rows=c.execute('SELECT record_id,revision FROM eric_app_changes WHERE workspace_id=%s ORDER BY revision',(workspace,)).fetchall()
    assert [row[0] for row in rows]==['first','second']
    cursor=rows[0][1]
    assert c.execute('SELECT record_id FROM eric_app_changes WHERE workspace_id=%s AND revision>%s',(workspace,cursor)).fetchall()==[('second',)]


def test_old_publisher_and_older_snapshot_cannot_overwrite_new_data(db):
    c, workspace, _ = db
    epoch = uuid4()
    c.execute('SELECT eric_app_begin_publish(%s,%s)', (workspace,epoch))
    publish(c,workspace,payload={'version':1},epoch=epoch,generation=1)
    publish(c,workspace,payload={'version':2},epoch=epoch,generation=2)
    with pytest.raises(psycopg.errors.RaiseException, match='stale_projection'):
        publish(c,workspace,payload={'version':1},epoch=epoch,generation=1)
    new_epoch = uuid4()
    c.execute('SELECT eric_app_begin_publish(%s,%s)', (workspace,new_epoch))
    with pytest.raises(psycopg.errors.RaiseException, match='stale_projection'):
        publish(c,workspace,payload={'version':3},epoch=epoch,generation=3)
    publish(c,workspace,payload={'version':4},epoch=new_epoch,generation=1)
    assert c.execute('SELECT payload FROM eric_app_records WHERE workspace_id=%s', (workspace,)).fetchone()[0]['version'] == 4


@pytest.mark.parametrize('kind,limit',[(None,3),('captures',None),('captures',0),('commands',6)])
def test_claim_rejects_null_and_unbounded_inputs(db,kind,limit):
    c, workspace, _ = db
    with pytest.raises(psycopg.errors.RaiseException, match='invalid_claim'):
        c.execute('SELECT eric_app_claim_work(%s,%s,%s)', (workspace,kind,limit))


def test_capture_retry_is_one_record_and_rejects_changed_payload_and_revoked_device(db):
    c, workspace, device = db
    row, result = capture(c,workspace,device)
    assert result[1] == 'received'
    c.execute('SELECT * FROM eric_app_reserve_capture(%s,%s)', (workspace,Jsonb(row))).fetchall()
    assert c.execute('SELECT count(*) FROM eric_app_captures WHERE workspace_id=%s',(workspace,)).fetchone()[0] == 1
    with pytest.raises(psycopg.errors.RaiseException, match='idempotency_conflict'):
        c.execute('SELECT * FROM eric_app_reserve_capture(%s,%s)', (workspace,Jsonb({**row,'request_hash':digest('changed')})))
    c.execute('UPDATE eric_app_devices SET revoked_at=now() WHERE id=%s', (device,))
    with pytest.raises(psycopg.errors.RaiseException, match='device_revoked'):
        c.execute('SELECT * FROM eric_app_reserve_capture(%s,%s)', (workspace,Jsonb(row)))


def test_cannot_use_a_device_from_another_workspace(db):
    c, workspace, device = db
    other = c.execute('INSERT INTO eric_app_workspaces(owner_id) VALUES(%s) RETURNING id', (uuid4(),)).fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException, match='device_revoked'):
        capture(c,other,device)


def test_metadata_without_actual_private_objects_is_never_received(db):
    c, workspace, device = db
    blob = b'synthetic original'
    sha = hashlib.sha256(blob).hexdigest()
    resource = {'id':'capture-resource','filename':'fixture.pdf','mime':'application/pdf','sha256':sha,'bytes':len(blob),'part_count':1}
    c.execute('SELECT * FROM eric_app_register_resource(%s,%s)', (workspace,Jsonb(resource)))
    row, initial = capture(c,workspace,device,files=[{'resource_id':resource['id'],'sha256':sha,'bytes':len(blob)}])
    assert initial[1] == 'uploading'
    with pytest.raises(psycopg.errors.RaiseException, match='resource_missing'):
        c.execute('SELECT eric_app_register_part(%s,%s,0,%s,%s)', (workspace,resource['id'],sha,len(blob)))
    with pytest.raises(psycopg.errors.RaiseException, match='resource_incomplete'):
        c.execute('SELECT * FROM eric_app_commit_capture(%s,%s)', (workspace,row['id']))
    path = str(workspace)+'/'+sha
    c.execute("INSERT INTO storage.objects(bucket_id,name,metadata) VALUES('eric-app-private',%s,%s)", (path,Jsonb({'size':len(blob)})))
    c.execute('SELECT eric_app_register_part(%s,%s,0,%s,%s)', (workspace,resource['id'],sha,len(blob)))
    c.execute('DELETE FROM storage.objects WHERE name=%s', (path,))
    with pytest.raises(psycopg.errors.RaiseException, match='resource_missing'):
        c.execute('SELECT * FROM eric_app_finish_resource(%s,%s)', (workspace,resource['id']))
    c.execute("INSERT INTO storage.objects(bucket_id,name,metadata) VALUES('eric-app-private',%s,%s)", (path,Jsonb({'size':len(blob)})))
    c.execute('SELECT * FROM eric_app_finish_resource(%s,%s)', (workspace,resource['id']))
    received = c.execute('SELECT state FROM eric_app_commit_capture(%s,%s)', (workspace,row['id'])).fetchone()[0]
    assert received == 'received'
    with pytest.raises(psycopg.errors.RaiseException, match='resource_conflict'):
        c.execute('SELECT * FROM eric_app_register_resource(%s,%s)', (workspace,Jsonb({**resource,'sha256':digest('different')})))


def test_repartition_only_before_any_confirmed_part_keeps_the_same_original(db):
    c, workspace, _ = db
    resource={'id':'large-synthetic-original','filename':'original.pdf','mime':'application/pdf','sha256':digest('original'),'bytes':14_000_000,'part_count':1}
    c.execute('SELECT * FROM eric_app_register_resource(%s,%s)',(workspace,Jsonb(resource)))
    smaller={**resource,'part_count':4}
    assert c.execute('SELECT part_count,sha256 FROM eric_app_register_resource(%s,%s)',(workspace,Jsonb(smaller))).fetchone()==(4,resource['sha256'])
    part_sha=digest('first part')
    c.execute("INSERT INTO storage.objects(bucket_id,name,metadata) VALUES('eric-app-private',%s,%s)",(str(workspace)+'/'+part_sha,Jsonb({'size':4_000_000})))
    c.execute('SELECT eric_app_register_part(%s,%s,0,%s,4000000)',(workspace,resource['id'],part_sha))
    with pytest.raises(psycopg.errors.RaiseException,match='resource_conflict'):
        c.execute('SELECT * FROM eric_app_register_resource(%s,%s)',(workspace,Jsonb(resource)))
    with pytest.raises(psycopg.errors.RaiseException,match='invalid_part'):
        c.execute('SELECT eric_app_register_part(%s,%s,1,%s,14000000)',(workspace,resource['id'],part_sha))


def test_stale_worker_cannot_finish_or_renew_new_lease(db):
    c, workspace, device = db
    row, _ = capture(c,workspace,device)
    first = c.execute("SELECT eric_app_claim_work(%s,'captures',1)", (workspace,)).fetchone()[0][0]
    c.execute("UPDATE eric_app_captures SET lease_until=now()-interval '1 second' WHERE id=%s", (row['id'],))
    second = c.execute("SELECT eric_app_claim_work(%s,'captures',1)", (workspace,)).fetchone()[0][0]
    assert first['lease_token'] != second['lease_token']
    assert c.execute("SELECT eric_app_renew_work(%s,'captures',%s,%s)", (workspace,row['id'],first['lease_token'])).fetchone()[0] is False
    result = {'raw_item_id':'raw-original','local_receipt_id':'receipt-original'}
    with pytest.raises(psycopg.errors.RaiseException, match='stale_lease'):
        c.execute("SELECT eric_app_finish_work(%s,'captures',%s,%s,'processing',%s)", (workspace,row['id'],first['lease_token'],Jsonb(result)))
    c.execute("SELECT eric_app_finish_work(%s,'captures',%s,%s,'processing',%s)", (workspace,row['id'],second['lease_token'],Jsonb(result)))
    assert c.execute("SELECT eric_app_claim_work(%s,'captures',1)", (workspace,)).fetchone()[0] == []


def test_progress_is_cloud_owned_versioned_and_idempotent_even_after_later_changes(db):
    c, workspace, device = db
    publish(c,workspace)
    request = uuid4()
    args = (workspace,device,'review_progress','review-fixture',0,request,digest('read'),Jsonb({'completed':True}))
    first = c.execute('SELECT eric_app_set_progress(%s,%s,%s,%s,%s,%s,%s,%s)', args).fetchone()[0]
    revision = first['revision']
    second = c.execute('SELECT eric_app_set_progress(%s,%s,%s,%s,%s,%s,%s,%s)',
        (workspace,device,'review_progress','review-fixture',revision,uuid4(),digest('unread'),Jsonb({'completed':False}))).fetchone()[0]
    replay = c.execute('SELECT eric_app_set_progress(%s,%s,%s,%s,%s,%s,%s,%s)', args).fetchone()[0]
    assert replay == first
    actual = c.execute("SELECT payload FROM eric_app_records WHERE workspace_id=%s AND kind='review_progress'", (workspace,)).fetchone()[0]
    assert actual['completed'] is False and second['revision']>revision
    with pytest.raises(psycopg.errors.RaiseException, match='version_conflict'):
        c.execute('SELECT eric_app_set_progress(%s,%s,%s,%s,%s,%s,%s,%s)',
            (workspace,device,'review_progress','review-fixture',revision,uuid4(),digest('stale'),Jsonb({'completed':True})))
    with pytest.raises(psycopg.errors.RaiseException, match='reserved_record_kind'):
        publish(c,workspace,kind='review_progress',record_id='review-fixture',payload={'completed':True})


def test_command_result_and_final_projection_commit_together(db):
    c, workspace, device = db
    revision = publish(c,workspace,kind='event',record_id='event-fixture',payload={'version':1,'title':'Synthetic'})
    command = {'id':str(uuid4()),'device_id':str(device),'resource_key':'event:event-fixture','method':'PATCH',
        'path':'/events/event-fixture','body':{'expected_version':1},'request_hash':digest('edit'),'expected_revision':revision}
    c.execute('SELECT * FROM eric_app_enqueue_command(%s,%s)', (workspace,Jsonb(command)))
    claimed = c.execute("SELECT eric_app_claim_work(%s,'commands',1)", (workspace,)).fetchone()[0][0]
    records = [{'kind':'event','id':'event-fixture','payload':{'version':2},'source_hash':digest('v2')}]
    c.execute("SELECT eric_app_finish_work(%s,'commands',%s,%s,'completed',%s,%s)",
        (workspace,command['id'],claimed['lease_token'],Jsonb({'saved':True}),Jsonb(records)))
    assert c.execute('SELECT state FROM eric_app_commands WHERE id=%s', (command['id'],)).fetchone()[0] == 'completed'
    assert c.execute("SELECT payload FROM eric_app_records WHERE workspace_id=%s AND kind='event'", (workspace,)).fetchone()[0]['version'] == 2


def test_inbox_archive_is_shared_reversible_and_does_not_remove_originals(db):
    c,workspace,device=db
    for identity in ('first','second'):
        publish(c,workspace,'inbox',identity,{'title':'Synthetic '+identity})
        publish(c,workspace,'inbox_item',identity,{'title':'Synthetic '+identity,'managed':True,'needs_manual':True,'created_at':'2026-10-29T00:00:00Z'})
    c.execute("SELECT eric_app_set_progress(%s,%s,'inbox_progress','first',0,%s,%s,%s)",(workspace,device,uuid4(),digest('archive'),Jsonb({'archived':True})))
    active=c.execute("SELECT eric_app_inbox_page(%s,'all','submitted',0,20)",(workspace,)).fetchone()[0]
    archived=c.execute("SELECT eric_app_inbox_page(%s,'archived','all',0,20)",(workspace,)).fetchone()[0]
    assert active['total']==1 and active['items'][0]['id']=='second'
    assert archived['total']==1 and archived['items'][0]['id']=='first'
    revision=archived['items'][0]['payload']['archive']['_cloud']['revision']
    c.execute("SELECT eric_app_set_progress(%s,%s,'inbox_progress','first',%s,%s,%s,%s)",(workspace,device,revision,uuid4(),digest('restore'),Jsonb({'archived':False})))
    assert c.execute("SELECT eric_app_inbox_page(%s,'all','all',0,1)",(workspace,)).fetchone()[0]['total']==2
    assert c.execute("SELECT count(*) FROM eric_app_records WHERE workspace_id=%s AND kind='inbox'",(workspace,)).fetchone()[0]==2
