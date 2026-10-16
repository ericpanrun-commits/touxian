"""Real PostgreSQL adapter integration; synthetic keys and HTTP transports only."""
import json
import hashlib
import os
import secrets
import ssl
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psycopg
import pytest

from server.actions.database import PostgresDatabase, SUPABASE_CA
from server.actions.acceptance import RECEIPT, run_acceptance
from server.actions.migrate import export_snapshot, import_snapshot, render_import_batches, render_import_sql, validate_payload
from server.actions.notifications import PostgresNotificationStore
from server.actions.runtime import ActionsRuntime
from server.actions.store import PostgresOfficialStore
from server.actions.worker import emit_summary, run_once, source_due_order
from server.notifications.bark import BarkClient
from server.notifications.policy import NotificationPolicyConfig
from server.official_http import HTMLResult, PublicHTMLClient
from server.official_parsers import parse_article
from server import test_bark_notifications as bark_fixtures
from server.test_official_information import TODAY, document, listing, seed, source

MIGRATION=Path(__file__).resolve().parents[1]/'supabase/migrations/20261015042812_zqu_actions_state.sql'
config=bark_fixtures.config
store=bark_fixtures.store


@pytest.fixture
def pg_uninitialized():
    dsn=os.environ.get('ZQU_TEST_PG_DSN','')
    if not dsn:pytest.skip('Set ZQU_TEST_PG_DSN for the isolated native PostgreSQL integration suite.')
    target=urlsplit(dsn)
    if target.hostname!='127.0.0.1' or target.path!='/zqu_actions_tests' or target.username!='zqu_test':
        raise ValueError('Integration fixture refuses any non-isolated or remote database.')
    def connect(value,**options):
        options.update(sslmode='disable');options.pop('sslrootcert',None)
        return psycopg.connect(value,**options)
    # A fresh local-only database for every test. No schema rebuild or cascading
    # cleanup SQL exists in this fixture, and it can never target Supabase.
    fresh_name='zqu_actions_test_'+uuid4().hex
    with psycopg.connect(dsn,autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(fresh_name)))
    fresh_dsn=dsn.rsplit('/',1)[0]+'/'+fresh_name
    database=PostgresDatabase(fresh_dsn,connect=connect)
    yield database
    database.close()


@pytest.fixture
def pg(pg_uninitialized):
    database=pg_uninitialized
    with database.connection() as db:
        db.execute(MIGRATION.read_text(encoding='utf-8'))
    yield database


@pytest.fixture
def imported(pg,store,tmp_path):
    seed(store,id=1,title='旧车辆工程通知')
    with store.connection(write=True) as db:
        db.execute("UPDATE notification_preferences SET policy_json=? WHERE channel='bark'",(json.dumps(NotificationPolicyConfig(quiet_enabled=False).model_dump()),))
        db.execute("UPDATE official_source SET backfill_completed_at='2000-01-01T00:00:00+00:00' WHERE source_id IN ('unit-source','unit-other')")
    report=export_snapshot(store.path,tmp_path/'snapshot')
    payload=json.loads(Path(report['export']).read_text(encoding='utf-8'))
    result=import_snapshot(pg,payload)
    runtime=ActionsRuntime(environment='production',auto_sync=True,notification_authority=True,
        execution_verified=True,expected_database_id=result['database_id'],run_id='synthetic:1')
    path=tmp_path/'sources.json'
    path.write_text(json.dumps({'version':1,'sources':[source().model_dump()]}),encoding='utf-8')
    return runtime,path,payload


def clients(config, mapping=None):
    calls=[]
    bark=BarkClient(config,transport=httpx.MockTransport(lambda req:(calls.append(req) or httpx.Response(200,json={'code':200}))))
    mapping=mapping or {source().list_url:listing([(2,'[test] 2026级车辆工程竞赛报名通知',TODAY)]),
        'https://unit.zqu.edu.cn/info/1/2.htm':document('[test] 2026级车辆工程竞赛报名通知','本科生请报名。')}
    def factory(current,**options):
        def handler(request):
            body=mapping.get(str(request.url))
            if isinstance(body,Exception):raise body
            return httpx.Response(200,headers={'content-type':'text/html'},content=body or b'<ul></ul>')
        return PublicHTMLClient(current,transport=httpx.MockTransport(handler),resolver=lambda _:None,delay=0,retries=0,**options)
    return bark,factory,calls


@pytest.mark.parametrize('change',[{'GITHUB_ACTIONS':'false'},{'GITHUB_REPOSITORY':'evil/fork'},
    {'GITHUB_REPOSITORY':'sssssjw11/touxian'}, {'GITHUB_REF':'refs/heads/dev'},
    {'GITHUB_EVENT_NAME':'pull_request'}, {'ZQU_EXPECTED_REPOSITORY':'evil/fork'}])
def test_wrong_context_cannot_send_with_accidental_real_credentials(change):
    values={'APP_ENV':'production','AUTO_SYNC_ENABLED':'true','PRODUCTION_NOTIFICATION_AUTHORITY':'true',
        'NOTIFICATION_DRY_RUN':'false','GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'ericpanrun-commits/touxian',
        'GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'schedule',**change}
    runtime=replace(ActionsRuntime.from_env(values),storage_verified=True)
    assert not runtime.automatic_bark_allowed and not runtime.automatic_sync_allowed


def test_owner_repository_can_become_verified_production_authority():
    values={'APP_ENV':'production','AUTO_SYNC_ENABLED':'true','PRODUCTION_NOTIFICATION_AUTHORITY':'true',
        'NOTIFICATION_DRY_RUN':'false','GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'ericpanrun-commits/touxian',
        'GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'schedule'}
    runtime=ActionsRuntime.from_env(values)
    assert not runtime.automatic_bark_allowed and not runtime.automatic_sync_allowed
    verified=replace(runtime,storage_verified=True)
    assert verified.automatic_bark_allowed and verified.automatic_sync_allowed
    assert not replace(verified,dry_run=True).automatic_bark_allowed


def test_secrets_not_in_repr_or_job_summary(tmp_path,capsys):
    token=secrets.token_urlsafe(24)
    runtime=ActionsRuntime(database_url='postgres://user:'+token+'@example.supabase.co/postgres')
    assert token not in repr(runtime)
    target=tmp_path/'summary.md'
    emit_summary({'sent':1,'status':'ok','private_token':token},target)
    assert token not in capsys.readouterr().out+target.read_text()


def test_database_trust_anchor_is_the_official_public_supabase_root():
    pem=SUPABASE_CA.read_text(encoding='ascii')
    assert pem.count('-----BEGIN CERTIFICATE-----')==1
    assert 'PRIVATE KEY' not in pem
    assert hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()==(
        '807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa')
    context=ssl.create_default_context(cafile=str(SUPABASE_CA))
    assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
    assert len(context.get_ca_certs())==1


def test_bounded_cloud_batches_rotate_through_overdue_sources():
    rows=[{'id':'important','priority':'high','next_check_at':'2026-10-17T02:00:00+00:00'},
        {'id':'normal','priority':'normal','next_check_at':'2026-10-17T01:00:00+00:00'},
        {'id':'news','priority':'low','next_check_at':'2026-10-17T00:00:00+00:00'}]
    assert [row['id'] for row in sorted(rows,key=source_due_order)]==['news','normal','important']
    rows[2]['next_check_at']='2026-10-17T04:00:00+00:00'
    assert sorted(rows,key=source_due_order)[0]['id']=='normal'


def test_equal_source_deadlines_preserve_priority_and_deterministic_order():
    rows=[{'id':name,'priority':priority,'next_check_at':None}
        for name,priority in [('news','low'),('high-b','high'),('normal','normal'),('high-a','high')]]
    assert [row['id'] for row in sorted(rows,key=source_due_order)]==['high-a','high-b','normal','news']


def test_migration_is_idempotent_and_private(pg):
    with pg.connection() as db:
        db.execute(MIGRATION.read_text(encoding='utf-8'))
        assert not db.execute("SELECT has_schema_privilege('anon','zqu_worker','usage') AS allowed").fetchone()['allowed']
        assert not db.execute("SELECT has_table_privilege('authenticated','zqu_worker.items','select') AS allowed").fetchone()['allowed']
        assert not db.execute("SELECT has_table_privilege('anon','zqu_worker.cloud_status','select') AS allowed").fetchone()['allowed']
        assert not db.execute("SELECT has_table_privilege('anon','zqu_worker.cloud_status','update') AS allowed").fetchone()['allowed']


def test_migration_contains_no_destructive_statements_or_external_ddl():
    import re
    text=re.sub(r'--[^\n]*','',MIGRATION.read_text())
    assert not re.search(r'\b(DROP|DELETE|TRUNCATE|CASCADE|UPDATE)\b',text,re.I)
    assert not re.search(r'\b(public|auth|storage|extensions)\s*\.',text,re.I)


def test_unmarked_schema_collision_aborts_without_changing_its_data_or_acl(pg_uninitialized):
    database=pg_uninitialized
    with database.connection() as db:
        db.execute('CREATE SCHEMA zqu_worker')
        db.execute('CREATE TABLE zqu_worker.unrelated_business(id integer PRIMARY KEY,value text)')
        db.execute("INSERT INTO zqu_worker.unrelated_business VALUES(1,'keep-me')")
        before=db.execute("SELECT nspacl::text AS acl FROM pg_namespace WHERE nspname='zqu_worker'").fetchone()
    with pytest.raises(psycopg.errors.RaiseException,match='ownership marker'):
        with database.connection() as db:db.execute(MIGRATION.read_text())
    with database.connection() as db:
        assert db.execute('SELECT * FROM zqu_worker.unrelated_business').fetchone()=={'id':1,'value':'keep-me'}
        assert db.execute("SELECT nspacl::text AS acl FROM pg_namespace WHERE nspname='zqu_worker'").fetchone()==before
        assert db.execute("SELECT to_regclass('zqu_worker.items') AS item").fetchone()['item'] is None


def test_unmarked_table_collision_in_owned_namespace_is_not_adopted(pg_uninitialized):
    database=pg_uninitialized
    with database.connection() as db:
        db.execute('CREATE SCHEMA zqu_worker')
        db.execute("COMMENT ON SCHEMA zqu_worker IS 'zqu-workspace:actions-state:v1'")
        db.execute('CREATE TABLE zqu_worker.meta(value text)')
        db.execute("INSERT INTO zqu_worker.meta VALUES('keep-me')")
    with pytest.raises(psycopg.errors.RaiseException,match='name collision'):
        with database.connection() as db:db.execute(MIGRATION.read_text())
    with database.connection() as db:
        assert db.execute('SELECT * FROM zqu_worker.meta').fetchone()=={'value':'keep-me'}


def test_extra_business_object_in_marked_namespace_is_not_repermissioned(pg_uninitialized):
    database=pg_uninitialized
    with database.connection() as db:
        db.execute('CREATE SCHEMA zqu_worker')
        db.execute("COMMENT ON SCHEMA zqu_worker IS 'zqu-workspace:actions-state:v1'")
        db.execute('CREATE TABLE zqu_worker.unrelated_business(value text)')
        db.execute("INSERT INTO zqu_worker.unrelated_business VALUES('preserve')")
        before=db.execute("SELECT nspacl::text AS acl FROM pg_namespace WHERE nspname='zqu_worker'").fetchone()
    with pytest.raises(psycopg.errors.RaiseException,match='Unexpected object'):
        with database.connection() as db:db.execute(MIGRATION.read_text())
    with database.connection() as db:
        assert db.execute('SELECT * FROM zqu_worker.unrelated_business').fetchone()=={'value':'preserve'}
        assert db.execute("SELECT nspacl::text AS acl FROM pg_namespace WHERE nspname='zqu_worker'").fetchone()==before


def test_migration_preserves_protected_schema_rows_columns_rls_and_permissions(pg_uninitialized):
    database=pg_uninitialized
    def snapshot(db):
        result={}
        for schema in ['public','auth','storage','extensions']:
            result[schema]={
                'rows':db.execute(f'SELECT * FROM {schema}.business_sentinel').fetchall(),
                'catalog':db.execute("SELECT relacl::text AS acl,relrowsecurity FROM pg_class WHERE oid=%s::regclass",(schema+'.business_sentinel',)).fetchone(),
                'columns':db.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema=%s AND table_name='business_sentinel' ORDER BY ordinal_position",(schema,)).fetchall()}
        return result
    with database.connection() as db:
        for schema in ['public','auth','storage','extensions']:
            db.execute(f'CREATE SCHEMA IF NOT EXISTS {schema}')
            db.execute(f'CREATE TABLE {schema}.business_sentinel(id integer PRIMARY KEY,value text)')
            db.execute(f"INSERT INTO {schema}.business_sentinel VALUES(1,'preserve')")
        before=snapshot(db)
        db.execute(MIGRATION.read_text())
        db.execute(MIGRATION.read_text())
        assert snapshot(db)==before
        tables=db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='zqu_worker' ORDER BY table_name").fetchall()
        assert len(tables)==10
        assert db.execute("SELECT to_regclass('public.zqu_worker_status') AS old_status").fetchone()['old_status'] is None


def test_reapply_preserves_imported_school_rows_and_baseline(pg,imported):
    with pg.connection() as db:
        before={table:db.execute('SELECT to_jsonb(t) AS row FROM zqu_worker.'+table+' t ORDER BY 1').fetchall()
                for table in ['meta','sources','items','notification_events']}
        db.execute(MIGRATION.read_text())
        for table,rows in before.items():
            assert db.execute('SELECT to_jsonb(t) AS row FROM zqu_worker.'+table+' t ORDER BY 1').fetchall()==rows


def test_foreign_keys_stay_inside_school_schema_and_never_cascade(pg):
    with pg.connection() as db:
        keys=db.execute("""SELECT k.confdeltype,n.nspname AS target_schema FROM pg_constraint k
            JOIN pg_class t ON t.oid=k.conrelid JOIN pg_namespace s ON s.oid=t.relnamespace
            JOIN pg_class r ON r.oid=k.confrelid JOIN pg_namespace n ON n.oid=r.relnamespace
            WHERE k.contype='f' AND s.nspname='zqu_worker'""").fetchall()
        assert keys and all(k['confdeltype']=='a' and k['target_schema']=='zqu_worker' for k in keys)


def test_canonical_placeholder_merge_preserves_history_without_cascade(pg,imported):
    _,_,payload=imported
    repository=PostgresOfficialStore(pg)
    original=payload['items'][0]
    school_source=source('unit-other')
    url='https://unit.zqu.edu.cn/info/1/new-alias.htm'
    placeholder=repository.discover(school_source,{'url':url,'title':'旧车辆工程通知','published_at':TODAY})
    article=parse_article(document('旧车辆工程通知'),original['canonical_url'],school_source,{'title':'旧车辆工程通知','published_at':TODAY})
    with pg.connection() as db:
        before=db.execute('SELECT to_jsonb(t) AS row FROM zqu_worker.notification_events t ORDER BY 1').fetchall()
    merged=repository.collect(school_source,url,original['canonical_url'],article,HTMLResult(original['canonical_url'],b'',200))
    assert merged['raw_item_id']==original['id']
    with pg.connection() as db:
        assert not db.execute('SELECT 1 FROM zqu_worker.items WHERE id=%s',(placeholder['raw_item_id'],)).fetchone()
        assert db.execute('SELECT to_jsonb(t) AS row FROM zqu_worker.notification_events t ORDER BY 1').fetchall()==before
        assert db.execute('SELECT item_id FROM zqu_worker.aliases WHERE url=%s',(url,)).fetchone()['item_id']==original['id']


def test_import_counts_baseline_dedup_and_idempotency(pg,imported):
    runtime,path,payload=imported
    with pg.connection() as db:
        for table,count in validate_payload(payload).items():
            assert db.execute('SELECT count(*) AS n FROM zqu_worker.'+table).fetchone()['n']==count
        meta=db.execute('SELECT * FROM zqu_worker.meta').fetchone()
        assert meta['activation_at']==payload['meta']['activation_at'] and not meta['first_dry_run_verified']
    assert import_snapshot(pg,payload)['already_imported']


def test_corrupted_export_cannot_change_production(pg,imported):
    _,_,payload=imported
    payload['meta']['activation_at']='altered'
    with pytest.raises(ValueError,match='校验和'):import_snapshot(pg,payload)


def test_connector_import_is_atomic_quoted_and_refuses_overwrite(pg,store,tmp_path):
    import hashlib
    seed(store,id=1,title='旧车辆工程通知')
    report=export_snapshot(store.path,tmp_path/'connector-snapshot')
    payload=json.loads(Path(report['export']).read_text(encoding='utf-8'))
    payload['items'][0]['body']['original_text']="quoted ' text \\ slash; $$; $zqu_import$ -- 学校"
    payload['meta'].pop('snapshot_hash')
    payload['meta']['snapshot_hash']=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    statement=render_import_sql(payload)
    with pg.connection() as db:db.execute(statement)
    with pg.connection() as db:
        assert db.execute('SELECT body FROM zqu_worker.items').fetchone()['body']==payload['items'][0]['body']
        assert db.execute('SELECT activation_at FROM zqu_worker.meta').fetchone()['activation_at']==payload['meta']['activation_at']
    with pytest.raises(psycopg.errors.RaiseException,match='not empty'):
        with pg.connection() as db:db.execute(statement)


def test_connector_import_rolls_back_all_rows_if_a_late_table_fails(pg,store,tmp_path):
    import hashlib
    seed(store,id=1,title='旧车辆工程通知')
    report=export_snapshot(store.path,tmp_path/'connector-failure')
    payload=json.loads(Path(report['export']).read_text(encoding='utf-8'))
    payload['notification_events'][0]['status']='invalid'
    payload['meta'].pop('snapshot_hash')
    payload['meta']['snapshot_hash']=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with pytest.raises(psycopg.errors.CheckViolation):
        with pg.connection() as db:db.execute(render_import_sql(payload))
    with pg.connection() as db:
        for table in ['sources','items','source_refs','aliases','notification_events','meta']:
            assert db.execute('SELECT count(*) AS n FROM zqu_worker.'+table).fetchone()['n']==0


def test_connector_staging_resumes_exact_rows_and_cannot_activate_incomplete_import(pg,store,tmp_path):
    seed(store,id=1,title='旧车辆工程通知')
    report=export_snapshot(store.path,tmp_path/'staged-snapshot')
    payload=json.loads(Path(report['export']).read_text(encoding='utf-8'))
    batches=render_import_batches(payload,max_bytes=3000)
    with pg.connection() as db:db.execute(batches[0])
    with pytest.raises(psycopg.errors.RaiseException,match='count mismatch'):
        with pg.connection() as db:db.execute(batches[-1])
    with pytest.raises(ValueError,match='尚未完成静默迁移'):PostgresOfficialStore(pg).meta()
    for statement in batches[:-1]:
        with pg.connection() as db:db.execute(statement)
        with pg.connection() as db:db.execute(statement)
    with pg.connection() as db:db.execute(batches[-1])
    assert PostgresOfficialStore(pg).meta()['activation_at']==payload['meta']['activation_at']
    with pytest.raises(psycopg.errors.RaiseException,match='already activated'):
        with pg.connection() as db:db.execute(batches[0])


def test_connector_staging_rejects_conflicting_existing_rows(pg,store,tmp_path):
    seed(store,id=1,title='旧车辆工程通知')
    report=export_snapshot(store.path,tmp_path/'staged-conflict')
    payload=json.loads(Path(report['export']).read_text(encoding='utf-8'))
    first=render_import_batches(payload)[0]
    with pg.connection() as db:db.execute(first)
    with pg.connection() as db:db.execute("UPDATE zqu_worker.sources SET config='{}' WHERE id=%s",(payload['sources'][0]['id'],))
    with pytest.raises(psycopg.errors.RaiseException,match='row mismatch'):
        with pg.connection() as db:db.execute(first)
    with pg.connection() as db:
        assert db.execute('SELECT count(*) AS n FROM zqu_worker.meta').fetchone()['n']==0


def test_dry_then_fresh_runs_automatically_send_once_and_never_restart_duplicate(pg,imported,config):
    runtime,path,_=imported
    bark,factory,calls=clients(config)
    dry=run_once(runtime,pg,bark_config=config,client=bark,client_factory=factory,config_path=path)
    assert dry['succeeded']==1 and dry['would_send']==1 and dry['sent']==0 and calls==[]
    active=replace(runtime,dry_run=False,run_id='synthetic:2')
    live=run_once(active,pg,bark_config=config,client=bark,client_factory=factory,config_path=path)
    assert live['sent']==1 and len(calls)==1
    # New connection objects model a new runner; all authority lives in Postgres.
    fresh=PostgresDatabase(pg._dsn,connect=pg._connect)
    try:again=run_once(replace(active,run_id='synthetic:3'),fresh,bark_config=config,client=bark,client_factory=factory,config_path=path)
    finally:fresh.close()
    assert again['sent']==0 and len(calls)==1


def test_live_run_is_refused_before_first_verified_dry_run(pg,imported,config):
    runtime,path,_=imported
    with pytest.raises(ValueError,match='静默验证'):
        run_once(replace(runtime,dry_run=False),pg,bark_config=config,config_path=path)


def test_one_failed_source_keeps_success_and_exits_without_global_failure(pg,imported,config):
    runtime,path,_=imported
    path.write_text(json.dumps({'version':1,'sources':[source().model_dump(),source('unit-other').model_dump()]}),encoding='utf-8')
    bark,factory,calls=clients(config,{source().list_url:listing([(1,'旧车辆工程通知',TODAY)]),source('unit-other').list_url:httpx.ConnectError('synthetic')})
    result=run_once(runtime,pg,bark_config=config,client=bark,client_factory=factory,config_path=path)
    assert result['status']=='partial' and result['succeeded']==1 and result['failed']==1 and not calls


def test_hourly_priority_intervals_and_source_health_survive_new_connections(pg,imported):
    repository=PostgresOfficialStore(pg)
    repository.configure([source(),source('low').model_copy(update={'priority':'low','polling_interval':14400})])
    for identifier in ['unit-source','low']:
        run=repository.begin(identifier)
        repository.finish(identifier,{'new':0,'changed':0,'unchanged':0,'requests':1},run_id=run)
    with pg.connection() as db:
        rows=db.execute("SELECT id,extract(epoch FROM next_check_at-clock_timestamp()) AS seconds FROM zqu_worker.sources WHERE id IN ('unit-source','low') ORDER BY id").fetchall()
    assert 14380<float(rows[0]['seconds'])<=14400
    assert 3580<float(rows[1]['seconds'])<=3600


def test_atomic_claim_two_connections_and_unknown_request_is_never_retried(pg,imported,config):
    runtime,path,_=imported
    bark,factory,calls=clients(config)
    run_once(runtime,pg,bark_config=config,client=bark,client_factory=factory,config_path=path)
    official=PostgresOfficialStore(pg);assert official.acquire_lease('claim-test','actions',900)
    at=datetime.now(timezone.utc)
    repos=[PostgresNotificationStore(pg,'claim-test') for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda r:r.claim(config,at=at),repos))
    claimed=[row for row in rows if row]
    assert len(claimed)==1
    assert repos[0].start_request(claimed[0],at=at)
    repos[0].recover(at=at+timedelta(minutes=3))
    assert repos[1].claim(config,at=at+timedelta(minutes=3)) is None and not calls
    with pg.connection() as db:
        assert db.execute('SELECT last_error FROM zqu_worker.notification_events WHERE id=%s',(claimed[0]['id'],)).fetchone()['last_error']=='delivery_unknown'


def test_workflow_has_hourly_schedule_no_paid_runner_or_docker():
    import yaml
    path=Path(__file__).resolve().parents[3]/'.github/workflows/zqu-official-info.yml'
    workflow=yaml.safe_load(path.read_text())
    trigger=workflow.get('on',workflow.get(True))
    assert len(trigger['schedule'])==1
    minute,*period=trigger['schedule'][0]['cron'].split()
    assert minute.isdigit() and 0<=int(minute)<60
    assert period==['*','*','*','*']
    assert workflow['concurrency']['cancel-in-progress'] is False
    job=workflow['jobs']['sync']
    assert job['runs-on']=='ubuntu-24.04' and job['timeout-minutes']==12
    text=path.read_text()
    assert 'server.actions.worker' in text and 'upload-artifact' not in text and 'docker' not in text.lower()
    assert trigger['workflow_dispatch']['inputs']['mode']['default']=='cycle'
    assert trigger['workflow_dispatch']['inputs']['mode']['options']==['cycle','acceptance']


def test_cloud_acceptance_sends_one_scoped_pipeline_and_preserves_ordinary_queue(pg,imported,config):
    runtime,path,_=imported
    bark,factory,calls=clients(config)
    run_once(runtime,pg,bark_config=config,client=bark,client_factory=factory,config_path=path)
    with pg.connection() as db:
        before=db.execute('SELECT id,status,dedup_key FROM zqu_worker.notification_events ORDER BY id').fetchall()
        baseline=db.execute('SELECT activation_at FROM zqu_worker.meta').fetchone()
    active=replace(runtime,dry_run=False)
    result=run_acceptance(active,pg,event='workflow_dispatch',bark_config=config,client=bark)
    assert result['status']=='ok' and result['sent']==1 and result['low_relevance_suppressed']==1
    assert len(calls)==1 and '[test]' in json.loads(calls[0].content)['title']
    with pg.connection() as db:
        assert db.execute('SELECT id,status,dedup_key FROM zqu_worker.notification_events ORDER BY id').fetchall()==before
        assert db.execute('SELECT activation_at FROM zqu_worker.meta').fetchone()==baseline
        assert db.execute("SELECT count(*) AS n FROM zqu_worker.items WHERE body->>'acceptance_test'='true'").fetchone()['n']==0
        assert db.execute('SELECT owner FROM zqu_worker.leases WHERE name=%s',(RECEIPT,)).fetchone()['owner']=='accepted'
    fresh=PostgresDatabase(pg._dsn,connect=pg._connect)
    try:again=run_acceptance(active,fresh,event='workflow_dispatch',bark_config=config,client=bark)
    finally:fresh.close()
    assert again['sent']==0 and again['duplicate_prevented']==1 and len(calls)==1


def test_acceptance_refuses_schedule_dry_run_and_unverified_first_cycle(pg,imported,config):
    runtime,_,_=imported
    bark,_,calls=clients(config)
    for current,event in [(replace(runtime,dry_run=False),'schedule'),(runtime,'workflow_dispatch')]:
        with pytest.raises(ValueError,match='手动生产'):run_acceptance(current,pg,event=event,bark_config=config,client=bark)
    with pytest.raises(ValueError,match='静默验证'):
        run_acceptance(replace(runtime,dry_run=False),pg,event='workflow_dispatch',bark_config=config,client=bark)
    assert not calls
    with pg.connection() as db:
        assert db.execute('SELECT count(*) AS n FROM zqu_worker.leases WHERE name=%s',(RECEIPT,)).fetchone()['n']==0


def test_acceptance_retryable_failure_spends_budget_and_does_not_retry(pg,imported,config):
    runtime,path,_=imported
    bark,factory,_=clients(config)
    run_once(runtime,pg,bark_config=config,client=bark,client_factory=factory,config_path=path)
    calls=[]
    failing=BarkClient(config,transport=httpx.MockTransport(lambda req:(calls.append(req) or httpx.Response(500))))
    active=replace(runtime,dry_run=False)
    result=run_acceptance(active,pg,event='workflow_dispatch',bark_config=config,client=failing)
    again=run_acceptance(active,pg,event='workflow_dispatch',bark_config=config,client=failing)
    assert result['status']=='failed' and again['duplicate_prevented']==1 and len(calls)==1
    with pg.connection() as db:
        assert db.execute("SELECT count(*) AS n FROM zqu_worker.notification_events WHERE metadata->>'acceptance_test'='true'").fetchone()['n']==0
        assert db.execute('SELECT owner FROM zqu_worker.leases WHERE name=%s',(RECEIPT,)).fetchone()['owner']=='failed_or_unknown'


def test_regular_worker_cannot_claim_or_recover_abandoned_acceptance_event(pg,imported,config):
    runtime,path,_=imported
    bark,factory,calls=clients(config)
    run_once(runtime,pg,bark_config=config,client=bark,client_factory=factory,config_path=path)
    with pg.connection() as db:
        db.execute("UPDATE zqu_worker.notification_events SET metadata=metadata||'{\"acceptance_test\":true}'::jsonb WHERE status='pending'")
    official=PostgresOfficialStore(pg);assert official.acquire_lease('orphan-test','actions',900)
    repo=PostgresNotificationStore(pg,'orphan-test')
    at=datetime.now(timezone.utc)
    assert repo.claim(config,at=at) is None
    with pg.connection() as db:
        db.execute("UPDATE zqu_worker.notification_events SET status='sending',attempted_at=%s WHERE metadata->>'acceptance_test'='true'",(at-timedelta(minutes=4),))
    repo.recover(at=at)
    with pg.connection() as db:
        assert db.execute("SELECT status FROM zqu_worker.notification_events WHERE metadata->>'acceptance_test'='true'").fetchone()['status']=='sending'
    assert calls==[]


def test_acceptance_unknown_previous_attempt_never_resends(pg,imported,config):
    runtime,path,_=imported
    bark,factory,calls=clients(config)
    run_once(runtime,pg,bark_config=config,client=bark,client_factory=factory,config_path=path)
    with pg.connection() as db:
        db.execute("INSERT INTO zqu_worker.leases(name,owner,expires_at) VALUES(%s,'reserved','9999-01-01')",(RECEIPT,))
    result=run_acceptance(replace(runtime,dry_run=False),pg,event='workflow_dispatch',bark_config=config,client=bark)
    assert result['status']=='failed' and result['duplicate_prevented']==1 and calls==[]


def test_acceptance_low_relevance_policy_failure_stops_before_any_push(pg,imported,config):
    runtime,path,_=imported
    bark,factory,calls=clients(config)
    run_once(runtime,pg,bark_config=config,client=bark,client_factory=factory,config_path=path)
    with pg.connection() as db:
        db.execute("UPDATE zqu_worker.meta SET policy=policy||'{\"minimum_relevance\":0}'::jsonb WHERE id=1")
    with pytest.raises(ValueError,match='未发送测试通知'):
        run_acceptance(replace(runtime,dry_run=False),pg,event='workflow_dispatch',bark_config=config,client=bark)
    assert calls==[]
    with pg.connection() as db:
        assert db.execute('SELECT count(*) AS n FROM zqu_worker.leases WHERE name=%s',(RECEIPT,)).fetchone()['n']==0
