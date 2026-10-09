"""Civil precision, DST and additive migration acceptance; no OS changes."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from server.personal_models import DeadlineInput, CollectionInput
from server.personal_store import PersonalStore
from server.planning.time import civil, reminder_civil, resolve, stamp, state

UTC = timezone.utc


def deadline(**changes):
    return {"value": "2026-10-12", "precision": "date", "confirmed_by_user": True,
            "timezone": "Asia/Shanghai", **changes}


def test_date_only_is_date_throughout_day():
    value = deadline()
    assert resolve(value).start == date(2026, 10, 12)
    assert not isinstance(resolve(value).start, datetime)
    for hour in (0, 9, 23):
        assert state(value, at=datetime(2026,10,12,hour,59,tzinfo=ZoneInfo("Asia/Shanghai")), display_zone=ZoneInfo("Asia/Shanghai"))["state"] == "today"
    assert value["value"] == "2026-10-12"


def test_exact_time_has_correct_instant_and_overdue():
    value = deadline(value="2026-10-12T14:30", precision="minute")
    assert stamp(resolve(value).start) == "2026-10-12T06:30:00.000000+00:00"
    assert state(value, at=datetime(2026,10,12,7,tzinfo=UTC))["state"] == "overdue"


@pytest.mark.parametrize("changes", [
    {"confirmed_by_user": False}, {"uncertain": True},
    {"precision": "unknown", "value": ""}, {"value": "2026-02-30"},
    {"precision": "minute", "value": "2026-10-12T14:30", "timezone": "No/SuchZone"},
    {"precision": "range", "range_precision": "date", "end_value": "2026-10-11"},
])
def test_unresolved_fact_has_no_projection(changes):
    assert resolve(deadline(**changes)) is None
    assert state(deadline(**changes))["state"] == "needs_confirmation"


def test_confirming_uncertainty_enables_projection_only_after_resolution():
    value = deadline(uncertain=True,confirmed_by_user=False)
    assert resolve(value) is None
    value.update(uncertain=False,confirmed_by_user=True)
    assert resolve(value)


def test_range_preserves_both_boundaries():
    result = resolve(deadline(precision="range",range_precision="date",end_value="2026-10-15"))
    assert (result.start,result.end,result.all_day) == (date(2026,10,12),date(2026,10,15),True)


def test_dst_gap_is_rejected_as_fact_but_reminder_moves_forward():
    tz = ZoneInfo("America/New_York")
    with pytest.raises(ValueError, match="不存在"):
        civil("2026-03-08T02:30",tz)
    result = reminder_civil(date(2026,3,8),"02:30",tz)
    assert result.hour == 3 and result.minute == 0


def test_dst_fold_requires_explicit_choice():
    tz = ZoneInfo("America/New_York")
    with pytest.raises(ValueError, match="两次"):
        civil("2026-11-01T01:30",tz)
    assert civil("2026-11-01T01:30",tz,1).astimezone(UTC) - civil("2026-11-01T01:30",tz,0).astimezone(UTC) == timedelta(hours=1)


def test_empty_timezone_tracks_system_without_changing_date(monkeypatch):
    from server.planning import time
    value = deadline(value="2026-10-12T09:00",precision="minute",timezone=None)
    monkeypatch.setattr(time,"system_zone",lambda:ZoneInfo("Asia/Tokyo"))
    first = resolve(value).start.astimezone(UTC)
    monkeypatch.setattr(time,"system_zone",lambda:ZoneInfo("Europe/London"))
    assert resolve(value).start.astimezone(UTC) != first
    assert value["value"] == "2026-10-12T09:00"


@pytest.mark.parametrize("changes", [
    {"precision":"unknown","value":"","confirmed_by_user":True},
    {"uncertain":True}, {"timezone":"bogus/zone"},
    {"precision":"range","range_precision":"date","end_value":"2026-10-11"},
    {"precision":"minute","value":"2026-03-08T02:30","timezone":"America/New_York"},
])
def test_invalid_manual_dates_are_rejected(changes):
    with pytest.raises(ValidationError):
        DeadlineInput(expected_version=1,label="报名",**deadline(**changes))


def test_unknown_manual_deadline_is_retained_as_unconfirmed():
    result = DeadlineInput(expected_version=1,label="待定",precision="unknown",confirmed_by_user=False)
    assert result.value == "" and result.uncertain


def test_v5_upgrade_preserves_every_old_value_and_foreign_key(tmp_path,monkeypatch):
    import server.personal_store as module
    store = PersonalStore(tmp_path / "upgrade.sqlite3")
    with monkeypatch.context() as patch:
        patch.setattr(module,"SCHEMA_VERSION",5)
        store.initialize()
    with store.connection(write=True) as db:
        db.execute("INSERT INTO source VALUES('s','manual_text','fixture','2026-01-01')")
        db.execute("INSERT INTO raw_item(id,source_id,kind,submission_hash,created_at) VALUES('r','s','manual_text','hash','2026-01-01')")
        db.execute("INSERT INTO raw_snapshot(id,raw_item_id,original_text,created_at,hash) VALUES('snap','r','截止日期 10月12日','2026-01-01','h')")
        db.execute("INSERT INTO event(id,raw_item_id,raw_snapshot_id,title,created_at,updated_at) VALUES('e','r','snap','历史事项','2026-01-01','2026-01-01')")
        db.execute("INSERT INTO deadline VALUES('d','e','截止','2026-10-12','date','Asia/Shanghai','10月12日',1,'2026-01-01','2026-01-01')")
        db.execute("INSERT INTO task(id,event_id,title,sort_order,deadline_id,origin,created_at,updated_at) VALUES('t','e','历史任务',0,'d','manual','2026-01-01','2026-01-01')")
        db.execute("INSERT INTO evidence(id,event_id,raw_snapshot_id,field_path,quote,start_offset,end_offset,created_at) VALUES('ev','e','snap','deadlines.d.value','10月12日',5,10,'2026-01-01')")
    tables = ['raw_snapshot','event','deadline','task','evidence','user_decision']
    with store.connection() as db:
        before = {name:[dict(row) for row in db.execute(f'SELECT * FROM {name}')] for name in tables}
    store.initialize()
    store.initialize()
    with store.connection() as db:
        for table,rows in before.items():
            after = [dict(row) for row in db.execute(f'SELECT * FROM {table}')]
            assert len(after) == len(rows)
            for old,new in zip(rows,after):
                assert {column:new[column] for column in old} == old
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert db.execute('SELECT raw_item_id FROM evidence').fetchone()[0] == 'r'
    assert store.get_event('e')['tasks'][0]['deadline_id'] == 'd'


def test_failed_migration_is_atomic(tmp_path,monkeypatch):
    import server.personal_store as module
    path = tmp_path/'migrations'
    path.mkdir()
    for original in module.MIGRATIONS.glob('*.sql'):
        (path/original.name).write_text(original.read_text(encoding='utf-8'),encoding='utf-8')
    store = PersonalStore(tmp_path/'failure.sqlite3')
    with monkeypatch.context() as patch:
        patch.setattr(module,'SCHEMA_VERSION',5)
        store.initialize()
    with (path/'006_planning_reminders.sql').open('a',encoding='utf-8') as stream:
        stream.write("\nINSERT INTO deadline(id,event_id,label,value,precision,confirmed_by_user,created_at,updated_at) VALUES('bad','missing','bad','2026-01-01','date',1,'x','x');\n")
    monkeypatch.setattr(module,'MIGRATIONS',path)
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError,match='validation'):
        store.initialize()
    with store.connection() as db:
        assert db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 5
        assert 'end_value' not in {row[1] for row in db.execute('PRAGMA table_info(deadline)')}
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='reminder'").fetchone()
