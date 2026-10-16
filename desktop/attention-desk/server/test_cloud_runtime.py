"""Phase 7.2 authority, bootstrap, authentication, persistence and migration gates."""
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import product
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from server.cloud_app import create_app
from server.cloud_store import CloudStore, ProcessLock, database_manifest, safe_backup, validate_storage
from server.notifications.service import NotificationDispatcher
from server.personal_store import PersonalStore
from server.runtime import RuntimeConfig
from server.test_bark_notifications import config, store, dispatcher  # noqa: F401 - shared pytest fixtures
from server.test_official_information import seed


@pytest.mark.parametrize('production,cloud,authority,storage,sync,bootstrap,dry',list(product([False,True],repeat=7)))
def test_automatic_bark_requires_every_authority_gate(production,cloud,authority,storage,sync,bootstrap,dry):
    runtime=RuntimeConfig(environment='production' if production else 'development',role='cloud' if cloud else 'desktop',
        notification_authority=authority,storage_verified=storage,auto_sync=sync,bootstrap=bootstrap,dry_run=dry)
    assert runtime.automatic_bark_allowed == (production and cloud and authority and storage and sync and not bootstrap and not dry)


def test_default_development_is_safe_and_public_identity_never_exposes_admin_token():
    runtime=RuntimeConfig.from_env({})
    assert not runtime.automatic_sync_allowed and not runtime.automatic_bark_allowed
    token=secrets.token_urlsafe(32)
    runtime=RuntimeConfig.from_env({'APP_ENV':'production','APP_RUNTIME':'cloud','CLOUD_ADMIN_TOKEN':token,'AUTO_SYNC_ENABLED':'true','PRODUCTION_NOTIFICATION_AUTHORITY':'true'})
    assert not runtime.automatic_bark_allowed  # Actual storage attestation has not happened.
    assert token not in repr(runtime)+json.dumps(runtime.public())


@pytest.mark.parametrize('changes',[{'APP_ENV':'unknown'},{'AUTO_SYNC_ENABLED':'maybe'},{'CORS_ALLOWED_ORIGINS':'*'},
    {'CORS_ALLOWED_ORIGINS':'https://name:password@host.test'},{'CORS_ALLOWED_ORIGINS':'https://host.test/path'},
    {'OFFICIAL_TICK_SECONDS':'0'},{'BACKUP_KEEP':'100'},{'INSTANCE_ID':'contains spaces'}])
def test_invalid_runtime_configuration_fails_closed(changes):
    with pytest.raises(ValueError):RuntimeConfig.from_env(changes)


def test_local_real_credentials_cannot_automatically_send_but_explicit_test_works(store,config):
    allowed,calls=dispatcher(store,config)
    local=NotificationDispatcher(store,config=config,client=allowed.client,runtime=RuntimeConfig())
    seed(store)
    assert local.tick()==0 and calls==[]
    assert not local.settings()['runtime']['automatic_bark']
    assert local.test_notification()['accepted'] and len(calls)==1


def test_dry_run_builds_real_queue_without_marking_sent_and_activation_resumes_once(store,config,caplog):
    service,calls=dispatcher(store,config)
    production=service.runtime
    service.runtime=replace(production,dry_run=True)
    saved=seed(store)
    with caplog.at_level('INFO'):
        service.tick();service.tick()
    assert calls==[] and service.repository.item_status(saved['raw_item_id'])['status']=='pending'
    assert 'notification_would_send' in caplog.text and 'notification_sent' not in caplog.text
    service.runtime=production
    service.tick();service.tick()
    assert len(calls)==1 and service.repository.item_status(saved['raw_item_id'])['status']=='sent'


def test_singleton_lease_concurrency_expiry_and_release_are_database_backed(store):
    runtime=RuntimeConfig(instance_id='test-worker')
    owners=[CloudStore(store,runtime) for _ in range(6)]
    for owner in owners:owner.initialize()
    at=datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(lambda owner:owner.scheduler_claim(at=at),owners))
    assert sum(results)==1
    winner=owners[results.index(True)]
    assert winner.scheduler_claim(at=at+timedelta(seconds=30))
    loser=next(owner for owner in owners if owner is not winner)
    assert not loser.scheduler_claim(at=at+timedelta(seconds=60))
    assert loser.scheduler_claim(at=at+timedelta(minutes=3))
    assert not winner.scheduler_claim(at=at+timedelta(minutes=3))
    winner.release();assert not winner.scheduler_claim(at=at+timedelta(minutes=3))
    loser.release();assert winner.scheduler_claim(at=at+timedelta(minutes=3))


def test_process_lock_is_exclusive_and_released_on_close(tmp_path):
    first,second=ProcessLock(tmp_path),ProcessLock(tmp_path)
    first.acquire()
    with pytest.raises(RuntimeError):second.acquire()
    first.close();second.acquire();second.close()


def test_ephemeral_path_and_escape_are_rejected(tmp_path):
    runtime=RuntimeConfig(environment='production',role='cloud',data_root=str(tmp_path),database_path=str(tmp_path/'production.sqlite3'))
    with pytest.raises(ValueError):validate_storage(runtime,is_mount=lambda _:False)
    with pytest.raises(ValueError):validate_storage(replace(runtime,database_path=str(tmp_path.parent/'escape.sqlite3')),is_mount=lambda _:True)
    assert validate_storage(runtime,is_mount=lambda _:True,filesystem=lambda _:'ext4')==(tmp_path.resolve(),(tmp_path/'production.sqlite3').resolve())


@pytest.mark.parametrize('filesystem',['tmpfs','ramfs','overlay','nfs',None])
def test_memory_overlay_and_remote_mounts_cannot_attest_persistence(tmp_path,monkeypatch,filesystem):
    runtime=RuntimeConfig(environment='production',role='cloud',data_root=str(tmp_path),database_path=str(tmp_path/'production.sqlite3'))
    monkeypatch.setattr('server.cloud_store.os',SimpleNamespace(name='posix'))
    with pytest.raises(ValueError,match='持久磁盘'):
        validate_storage(runtime,is_mount=lambda _:True,filesystem=lambda _:filesystem)


def test_safe_backups_integrity_manifest_and_bounded_retention(store,tmp_path):
    seed(store)
    expected=database_manifest(store.path)
    backups=tmp_path/'backups'
    for _ in range(5):last=safe_backup(store.path,backups,keep=3)
    assert len(list(backups.glob('production-*.sqlite3')))==3
    assert database_manifest(last)==expected
    reopened=PersonalStore(last);reopened.initialize()
    assert database_manifest(last)==expected


def test_passive_status_stale_detection_and_no_transport_side_effect(store):
    runtime=RuntimeConfig(environment='production',role='cloud',auto_sync=True,notification_authority=True,storage_verified=True)
    repository=CloudStore(store,runtime);repository.initialize()
    assert repository.status()['scheduler']=='stale'
    assert repository.scheduler_claim()
    assert repository.status()['scheduler']=='healthy'
    with store.connection(write=True) as db:db.execute("UPDATE worker_runtime SET last_scheduler_tick='2000-01-01T00:00:00+00:00'")
    assert repository.status()['status']=='degraded'
    assert not repository.status().get('admin_token')


def test_cloud_api_authentication_cors_and_admin_protection(store,config):
    service,calls=dispatcher(store,config)
    token=secrets.token_urlsafe(32)
    runtime=replace(service.runtime,admin_token=token,allowed_origins=('http://127.0.0.1:5173',))
    app=create_app();app.state.runtime=runtime;app.state.personal_store=store
    repository=CloudStore(store,runtime);repository.initialize();repository.scheduler_claim()
    app.state.official_monitor=SimpleNamespace(notifications=service,cloud_store=repository)
    client=TestClient(app)
    for path in ['/api/personal/official/feed','/api/personal/notifications/bark/settings','/api/personal/system/status']:
        assert client.get(path).status_code==401
    assert client.post('/api/personal/notifications/bark/test',json={}).status_code==401
    assert calls==[]
    headers={'Authorization':'Bearer '+token}
    assert client.get('/api/personal/system/status',headers=headers).status_code==200
    assert token not in client.get('/api/personal/system/status',headers=headers).text
    assert client.get('/api/personal/system/status',headers={**headers,'Origin':'https://evil.test'}).status_code==403
    assert client.get('/api/personal/system/status',headers={**headers,'Origin':'http://127.0.0.1:5173'}).status_code==200
    assert client.get('/health').json()=={'status':'ok'} and client.get('/health/ready').status_code==200
    assert calls==[]
    client.close()


def test_cloud_bootstrap_missing_db_stays_quiet_and_ready_is_false(tmp_path,monkeypatch):
    runtime=RuntimeConfig(environment='production',role='cloud',bootstrap=True,dry_run=True,admin_token=secrets.token_urlsafe(32),
        data_root=str(tmp_path),database_path=str(tmp_path/'missing.sqlite3'),expected_database_id='expected')
    monkeypatch.setattr('server.cloud_app.RuntimeConfig.from_env',lambda:runtime)
    monkeypatch.setattr('server.cloud_app.validate_storage',lambda config:(tmp_path,tmp_path/'missing.sqlite3'))
    with TestClient(create_app()) as client:
        assert client.get('/health').json()=={'status':'bootstrapping','database_accessible':False}
        assert client.get('/health/ready').status_code==503
        assert not (tmp_path/'missing.sqlite3').exists()


def test_cloud_rejects_wrong_database_before_mutating_it(store,monkeypatch):
    expected=database_manifest(store.path)
    runtime=RuntimeConfig(environment='production',role='cloud',bootstrap=True,admin_token=secrets.token_urlsafe(32),expected_database_id='wrong')
    monkeypatch.setattr('server.cloud_app.RuntimeConfig.from_env',lambda:runtime)
    monkeypatch.setattr('server.cloud_app.validate_storage',lambda config:(store.path.parent,store.path))
    with pytest.raises(RuntimeError,match='数据库身份'):
        with TestClient(create_app()):pass
    assert database_manifest(store.path)==expected


def test_migration_009_preserves_every_old_column_and_row(tmp_path,monkeypatch):
    monkeypatch.setattr('server.personal_store.SCHEMA_VERSION',8)
    personal=PersonalStore(tmp_path/'before.sqlite3');personal.initialize()
    with personal.connection() as db:
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_migrations'")]
        before={t:([r[1] for r in db.execute(f'PRAGMA table_info("{t}")')],[tuple(r) for r in db.execute(f'SELECT * FROM "{t}"')]) for t in tables}
    monkeypatch.setattr('server.personal_store.SCHEMA_VERSION',9);personal.initialize();personal.initialize()
    with personal.connection() as db:
        for table,(columns,rows) in before.items():
            assert [tuple(r) for r in db.execute('SELECT '+','.join('"'+c+'"' for c in columns)+f' FROM "{table}"')]==rows
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall()
