"""Actual SQLite transaction and crash/retry behavior, using synthetic domain data."""
import hashlib
from uuid import uuid4

import pytest

from server.cloud_sync.commands import CommandExecutor, execute_atomic
from server.personal_store import PersonalStore,PersonalError
from server.personal_models import CollectionInput


@pytest.fixture
def store(tmp_path):
    store=PersonalStore(tmp_path/'synthetic.sqlite3');store.initialize();return store


def command(path,body):
    return {'id':str(uuid4()),'method':'PATCH','path':path,'body':body}


def test_mutation_and_receipt_commit_once_even_when_the_cloud_ack_is_lost(store):
    event=store.create_collection(CollectionInput(original_text='Synthetic original.',title='Synthetic event'))
    cmd=command('/events/'+event['id'],{'expected_version':event['version'],'title':'Edited once','summary':'','category':'其他'})
    executor=CommandExecutor(store,None,None,None)
    result=executor.pure(cmd)
    again=executor.pure(cmd)
    assert result==again
    assert store.get_event(event['id'])['version']==event['version']+1
    with store.connection() as c: assert c.execute('SELECT count(*) FROM cloud_command_receipt').fetchone()[0]==1
    cmd['body']['title']='Different content'
    with pytest.raises(PersonalError,match='不同内容'):executor.pure(cmd)


def test_partial_domain_mutation_rolls_back_with_its_receipt(store):
    event=store.create_collection(CollectionInput(original_text='Synthetic original.',title='Before'))
    cmd=command('/synthetic',{})
    def interrupted(transaction):
        transaction.edit_event(event['id'],event['version'],{'title':'Must roll back','summary':'','category':'其他'})
        raise RuntimeError('Synthetic crash before receipt')
    with pytest.raises(RuntimeError):execute_atomic(store,cmd,interrupted)
    assert store.get_event(event['id'])['title']=='Before'
    assert store.get_event(event['id'])['version']==event['version']
    with store.connection() as c:assert c.execute('SELECT count(*) FROM cloud_command_receipt').fetchone()[0]==0


def test_stale_edits_do_not_overwrite_a_newer_device_change(store):
    event=store.create_collection(CollectionInput(original_text='Synthetic original.',title='Before'))
    store.edit_event(event['id'],event['version'],{'title':'Newer state','summary':'','category':'其他'})
    cmd=command('/events/'+event['id'],{'expected_version':event['version'],'title':'Stale state','summary':'','category':'其他'})
    with pytest.raises(PersonalError):CommandExecutor(store,None,None,None).pure(cmd)
    assert store.get_event(event['id'])['title']=='Newer state'


def test_v24_upgrade_preserves_existing_ids_facts_and_a_readable_backup(tmp_path,monkeypatch):
    import sqlite3
    from contextlib import closing
    from server.personal_store import SCHEMA_VERSION
    monkeypatch.setattr('server.personal_store.SCHEMA_VERSION',24)
    store=PersonalStore(tmp_path/'personal.sqlite3');store.initialize()
    event=store.create_collection(CollectionInput(original_text='Existing synthetic original.',title='Existing event'))
    before=store.get_event(event['id'])
    monkeypatch.setattr('server.personal_store.SCHEMA_VERSION',SCHEMA_VERSION)
    store.initialize()
    assert store.get_event(event['id'])==before
    with store.connection() as c:
        assert c.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==SCHEMA_VERSION
        assert c.execute('SELECT count(*) FROM cloud_command_receipt').fetchone()[0]==0
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    backup=store.path.with_name(store.path.name+f'.before-v{SCHEMA_VERSION}.backup')
    assert backup.is_file()
    with closing(sqlite3.connect(backup)) as c:
        assert c.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==24
        assert c.execute('SELECT id,title FROM event WHERE id=?',(event['id'],)).fetchone()==(event['id'],'Existing event')
