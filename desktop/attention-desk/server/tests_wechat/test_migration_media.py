import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

import server.personal_store as stores
from server.personal_models import CollectionInput
from server.personal_store import PersonalError
from server.wechat_collect.config import ChatConfig, SettingsDocument, WeChatConfig
from server.wechat_collect.media import MediaStore
from server.wechat_collect.models import Observation, ObservedMessage
from server.wechat_collect.scheduler import SchedulerPolicy
from server.wechat_collect.store import WeChatStore


def test_v9_backup_preserves_every_formal_row_and_block(tmp_path,monkeypatch):
    monkeypatch.setattr(stores,'SCHEMA_VERSION',9)
    db=stores.PersonalStore(tmp_path/'v9.sqlite3')
    db.initialize()
    event=db.create_collection(CollectionInput(original_text='迁移前原始通知',title='保留事项'))
    with db.connection(write=True) as c:
        c.execute("INSERT INTO raw_source_block(raw_snapshot_id,block_id,source_type,ordinal,text) VALUES (?,'b','text',0,'迁移前原始通知')",(event['snapshot']['id'],))
        originals={table:[tuple(row) for row in c.execute('SELECT * FROM '+table)] for table in ('source','raw_item','raw_snapshot','event','deadline','task','evidence','raw_source_block')}
    monkeypatch.setattr(stores,'SCHEMA_VERSION',10)
    db.initialize()
    with db.connection() as c:
        for table,rows in originals.items():
            assert [tuple(row) for row in c.execute('SELECT * FROM '+table)]==rows
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    backup=sqlite3.connect(tmp_path/'v9.sqlite3.before-v10.backup')
    try:
        assert backup.execute('SELECT max(version) FROM schema_migrations').fetchone()[0]==9
        assert backup.execute('SELECT count(*) FROM event').fetchone()[0]==1
    finally:
        backup.close()


def seed_image(personal):
    repo=WeChatStore(personal)
    repo.configure(SettingsDocument(wechat=WeChatConfig(priority_chats=[ChatConfig(id='images',name='合成图片测试群')])))
    scan,_=repo.begin_scan('synthetic',1)
    _,messages,_=repo.capture(scan['id'],repo.chat('images'),Observation('images','unverified',(ObservedMessage('',message_type='image',attachment_identity='synthetic'),),True,True))
    return messages[0]['id']


def test_ocr_cache_preserves_original_and_never_is_original_text(personal):
    policy=SchedulerPolicy(WeChatConfig(enabled=True),lambda:datetime.fromisoformat('2026-10-22T10:00:00+08:00'))
    media=MediaStore(personal,policy)
    media.spool.mkdir()
    image=media.spool/'synthetic.png'
    Image.new('RGB',(100,100),'white').save(image)
    mid=media.preserve(seed_image(personal),image)
    calls=[]
    def fake_ocr(path,work):
        calls.append(path)
        return [{'text':'合成 OCR 文字','bounding_box':{'x':0,'y':0,'width':50,'height':20},'raw_ocr_text':'合成 OCR 文字'}],{'engine':'fake'}
    first=media.ocr(mid,engine=fake_ocr,engine_version='fake-local-v1')
    second=media.ocr(mid,engine=fake_ocr,engine_version='fake-local-v1')
    assert len(calls)==1 and first['original_preserved'] and second['cache_hit']
    with personal.connection() as c:
        row=c.execute('SELECT * FROM wechat_media WHERE id=?',(mid,)).fetchone()
        assert Path(row['local_path']).is_file() and row['capture_kind']=='screenshot'
        assert c.execute('SELECT raw_text FROM wechat_message WHERE id=?',(row['message_id'],)).fetchone()[0]==''


def test_ocr_failure_and_night_never_runs_engine(personal):
    current=[datetime.fromisoformat('2026-10-22T10:00:00+08:00')]
    policy=SchedulerPolicy(WeChatConfig(enabled=True),lambda:current[0])
    media=MediaStore(personal,policy)
    media.spool.mkdir()
    image=media.spool/'synthetic.png'
    Image.new('RGB',(10,10)).save(image)
    mid=media.preserve(seed_image(personal),image)
    def broken(*args): raise RuntimeError('fake OCR failure')
    assert media.ocr(mid,engine=broken)['original_preserved']
    current[0]=current[0].replace(hour=1)
    with pytest.raises(PersonalError,match='quiet'):
        media.ocr(mid,engine=lambda *_:pytest.fail('Night OCR must not run'))


def test_failed_new_ocr_engine_preserves_previous_transcription_version(personal):
    policy=SchedulerPolicy(WeChatConfig(enabled=True),lambda:datetime.fromisoformat('2026-10-22T10:00:00+08:00'))
    media=MediaStore(personal,policy);media.spool.mkdir()
    image=media.spool/'notice.png';Image.new('RGB',(100,100),'white').save(image)
    mid=media.preserve(seed_image(personal),image)
    media.ocr(mid,engine=lambda *a:([{'text':'上一版有效文字与位置'}],{}),engine_version='local-v1')
    with personal.connection() as c:old=dict(c.execute('SELECT * FROM wechat_media WHERE id=?',(mid,)).fetchone())
    def broken(*a):raise ValueError('new recognizer failed')
    result=media.ocr(mid,engine=broken,engine_version='local-v2')
    assert result['status']=='needs_review'
    with personal.connection() as c:assert dict(c.execute('SELECT * FROM wechat_media WHERE id=?',(mid,)).fetchone())==old


def test_sqlite_lock_does_not_advance_cursor(personal):
    message=seed_image(personal)
    repo=WeChatStore(personal)
    before=repo.cursor('images')
    scan,_=repo.begin_scan('lock-test',1)
    lock=sqlite3.connect(personal.path,isolation_level=None)
    lock.execute('BEGIN IMMEDIATE')
    try:
        with pytest.raises(sqlite3.OperationalError):
            repo.capture(scan['id'],repo.chat('images'),Observation('images','unverified',(),True))
    finally:
        lock.rollback()
        lock.close()
    assert repo.cursor('images')==before
