"""Course -> human confirmation -> task/today, with v19 and temporal regressions."""
import asyncio
import copy
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from icalendar import Calendar

from server.course_api import router as course_router
from server.courses import CourseStore, normalized_name
from server.extraction_models import RawInput
from server.extraction_service import ExtractionService
from server.personal_api import router as personal_router
from server.personal_store import PersonalError, PersonalStore, encode
from server.planning.api import router as planning_router
from server.planning.query import PlanningQuery
from server.planning.ics import export
from server.reminders.store import ReminderStore
from server.review_api import router as review_router
from server.review_store import ReviewStore
from server.test_extraction_review import candidate, confirmation, TestSettings, FixtureProvider, finish

MATH = "course-math-ii"
NOW = datetime.fromisoformat("2026-10-27T08:00:00+08:00")
TEXT = "高等数学 II：请于2026年10月27日前完成练习并提交作业。"

@pytest.fixture
def store(tmp_path):
    s = PersonalStore(tmp_path/"personal.sqlite3")
    s.initialize()
    return s

@pytest.fixture
def client(store):
    app = FastAPI()
    for r in [personal_router,review_router,planning_router,course_router]:
        app.include_router(r)
    app.state.personal_store=store
    with TestClient(app) as c:
        yield c

def staged(store,text=TEXT):
    output = candidate(text,dates=[("2026年10月27日前","2026-10-27")],
                       actions=["完成练习并提交作业"],title="提交高数作业",category="课程作业")
    reviews=ReviewStore(store)
    raw=reviews.collect(RawInput(original_text=text,title_hint="高等数学作业",source_time="2026-10-26 10:00"))
    async def run():
        service=ExtractionService(store,TestSettings(),FixtureProvider(output))
        await service.start(raw["snapshot"]["id"])
        await finish(service)
        await service.close()
    asyncio.run(run())
    bundle=reviews.get(raw["snapshot"]["id"])
    payload=confirmation(bundle,bundle["extraction"]["parsed_output"])
    return reviews,bundle,payload

def formal(store,course_id=None,text=TEXT):
    reviews,bundle,payload=staged(store,text)
    payload.events[0].course_id=course_id
    result=reviews.confirm(bundle["snapshot"]["id"],payload)
    return result["events"][0],reviews,bundle,payload

def test_seeded_course_lookup_create_and_alias_idempotency(client,store):
    catalog=client.get("/api/personal/courses").json()
    assert catalog["total"]==1 and catalog["items"][0]["name"]=="高等数学 II"
    for name in ["高数","高等数学","高等数学 II"," 高等数学　Ⅱ ","高等数学ii"]:
        result=client.post("/api/personal/courses",json={"name":name})
        assert result.status_code==201
        assert result.json()["id"]==MATH and result.json()["created"] is False
        assert client.get("/api/personal/courses/resolve",params={"name":name}).json()["course"]["id"]==MATH
    assert CourseStore(store).list()["total"]==1
    assert client.get("/api/personal/courses/"+MATH).json()["name"]=="高等数学 II"

def test_other_course_creation_and_validation(client):
    first=client.post("/api/personal/courses",json={"name":" 大学物理 "}).json()
    second=client.post("/api/personal/courses",json={"name":"大学物理"}).json()
    assert first["created"] and second["id"]==first["id"] and not second["created"]
    assert client.post("/api/personal/courses",json={"name":"   "}).status_code==400
    assert client.post("/api/personal/courses",json={"name":"x"*121}).status_code==422
    assert client.get("/api/personal/courses/resolve",params={"name":"未知课程"}).json()["course"] is None

def test_suggestion_is_not_a_confirmed_fact(store):
    reviews,bundle,payload=staged(store)
    assert bundle["course_suggestions"]["event1"]["id"]==MATH
    assert bundle["course_suggestions"]["event1"]["origin"]=="rule"
    assert payload.events[0].course_id is None
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM event").fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM task").fetchone()[0]==0
    payload.events[0].course_id=MATH
    saved=reviews.confirm(bundle["snapshot"]["id"],payload)["events"][0]
    assert saved["course"]["name"]=="高等数学 II"
    assert saved["course_id"]==MATH and saved["context"]["review"]["course_id"]==MATH

def test_task_and_today_derive_course_without_duplicate_storage(store):
    saved,_,_,_=formal(store,MATH)
    task=saved["tasks"][0]
    assert task["course_id"]==MATH and task["course"]["name"]=="高等数学 II"
    today=PlanningQuery(store).today(at=NOW)
    row=next(i for i in today["items"] if i["event_id"]==saved["id"])
    assert row["course"]["id"]==MATH and row["tasks"][0]["course_id"]==MATH
    with store.connection() as c:
        assert "course_id" not in {r["name"] for r in c.execute("PRAGMA table_info(task)")}

def test_course_is_optional_and_non_course_flow_unaffected(store):
    text=TEXT.replace("高等数学 II","实验室安全教育")
    saved,_,_,_=formal(store,None,text)
    assert saved["course_id"] is None and saved["course"] is None
    assert len(saved["tasks"])==1 and saved["tasks"][0]["course"] is None
    row=next(i for i in PlanningQuery(store).today(at=NOW)["items"] if i["event_id"]==saved["id"])
    assert row["course"] is None

def test_manual_raw_suggestion_can_be_declined(store):
    reviews=ReviewStore(store)
    bundle=reviews.collect(RawInput(original_text="高数课程通知，暂无明确行动。"))
    assert bundle["course_suggestions"]["_default"]["id"]==MATH
    saved,_,_,_=formal(store,None)
    assert saved["course"] is None

def test_repeat_confirm_does_not_duplicate_task_course_or_matter(store):
    saved,reviews,bundle,payload=formal(store,MATH)
    again=reviews.confirm(bundle["snapshot"]["id"],payload)["events"][0]
    assert again["id"]==saved["id"] and again["tasks"][0]["id"]==saved["tasks"][0]["id"]
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM event").fetchone()[0]==1
        assert c.execute("SELECT count(*) FROM task").fetchone()[0]==1
        assert c.execute("SELECT count(*) FROM course").fetchone()[0]==1
    changed=payload.model_copy(deep=True);changed.events[0].course_id=None
    with pytest.raises(PersonalError,match="其他内容"):
        reviews.confirm(bundle["snapshot"]["id"],changed)

def test_empty_course_preserves_legacy_confirmation_fingerprint(store):
    saved,reviews,bundle,payload=formal(store,None)
    legacy=payload.model_dump(exclude={"expected_version"})
    for event in legacy["events"]:event.pop("course_id",None)
    expected=hashlib.sha256(encode(legacy).encode()).hexdigest()
    with store.connection() as c:
        assert c.execute("SELECT confirmation_hash FROM raw_review WHERE raw_snapshot_id=?",(bundle["snapshot"]["id"],)).fetchone()[0]==expected
    old_request=json.loads(payload.model_dump_json())
    for event in old_request["events"]:event.pop("course_id",None)
    from server.extraction_models import ConfirmationInput
    assert reviews.confirm(bundle["snapshot"]["id"],ConfirmationInput.model_validate(old_request))["events"][0]["id"]==saved["id"]

def test_invalid_course_confirmation_is_atomic(store):
    reviews,bundle,payload=staged(store)
    payload.events[0].course_id="missing-course"
    with pytest.raises(PersonalError,match="课程不存在"):
        reviews.confirm(bundle["snapshot"]["id"],payload)
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM event").fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM task").fetchone()[0]==0

def test_formal_course_change_clear_and_version_conflict(client,store):
    saved,_,_,_=formal(store,None)
    other=CourseStore(store).create("大学物理")
    endpoint="/api/personal/events/"+saved["id"]+"/course"
    linked=client.put(endpoint,json={"expected_version":saved["version"],"course_id":MATH})
    assert linked.status_code==200
    linked=linked.json()
    assert linked["tasks"][0]["course_id"]==MATH
    assert client.put(endpoint,json={"expected_version":saved["version"],"course_id":other["id"]}).status_code==409
    other_link=client.put(endpoint,json={"expected_version":linked["version"],"course_id":other["id"]}).json()
    assert other_link["tasks"][0]["course_id"]==other["id"]
    assert other_link["tasks"][0]["id"]==saved["tasks"][0]["id"]
    cleared=client.put(endpoint,json={"expected_version":other_link["version"],"course_id":None}).json()
    assert cleared["course"] is None and cleared["tasks"][0]["course"] is None
    repeated=client.put(endpoint,json={"expected_version":cleared["version"],"course_id":None}).json()
    assert repeated["version"]==cleared["version"]

def test_course_selection_survives_review_draft(store):
    reviews,bundle,payload=staged(store)
    draft=payload.model_dump(exclude={"expected_version","extraction_id"})
    draft["events"][0]["course_id"]=MATH
    saved=reviews.save_draft(bundle["snapshot"]["id"],bundle["version"],draft)
    assert saved["draft"]["events"][0]["course_id"]==MATH
    assert saved["course_suggestions"]["event1"]["id"]==MATH

def test_no_cross_candidate_suggestion_and_ambiguous_source(store):
    physics=CourseStore(store).create("大学物理")
    with store.connection() as c:
        assert CourseStore.suggest(c,"高数与大学物理请分别核对。") is None
        assert CourseStore.suggest(c,"高等数学 I 作业") is None
        assert CourseStore.suggest(c,"高等数学 III 作业") is None
        assert CourseStore.suggest(c,"高等数学Ⅱ 作业")["id"]==MATH
        suggestions=CourseStore.suggestions(c,{"original_text":"高数作业；班会通知"},{
            "parsed_output":{"events":[{"id":"math","actions":[{"evidence_ids":["q1"]}]},{"id":"meeting","actions":[{"evidence_ids":["q2"]}]}],
                             "evidence":[{"id":"q1","quote":"高数作业"},{"id":"q2","quote":"班会通知"}]}})
        assert suggestions["math"]["id"]==MATH and "meeting" not in suggestions

def test_deadline_reminder_identity_and_ics_dates_unchanged(store):
    saved,_,_,_=formal(store,None)
    reminders=ReminderStore(store)
    reminders.reconcile(at=NOW)
    with store.connection() as c:
        before=[tuple(r) for r in c.execute("SELECT dedupe_key,fact_key,scheduled_at,status FROM reminder ORDER BY id")]
    def calendar():
        values={}
        for e in Calendar.from_ical(export(store,future=False,include_terminal=True)).walk("VEVENT"):
            values[str(e["UID"])]=(e.decoded("DTSTART"),e.decoded("DTEND"),str(e["SUMMARY"]))
        return values
    calendar_before=calendar()
    changed=store.set_course(saved["id"],saved["version"],MATH)
    reminders.reconcile(at=NOW)
    with store.connection() as c:
        after=[tuple(r) for r in c.execute("SELECT dedupe_key,fact_key,scheduled_at,status FROM reminder ORDER BY id")]
    assert before==after
    assert changed["deadlines"]==saved["deadlines"]
    assert calendar()==calendar_before

def test_v19_migration_preserves_all_existing_columns_and_is_repeatable(tmp_path):
    path=tmp_path/"v19.sqlite3"
    migrations=Path(__file__).with_name("migrations")
    c=sqlite3.connect(path)
    c.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)")
    for version in range(1,20):
        c.executescript(next(migrations.glob(f"{version:03d}_*.sql")).read_text(encoding="utf-8"))
        c.execute("INSERT INTO schema_migrations VALUES (?,?)",(version,"2026-10-26"));c.commit()
    c.execute("INSERT INTO source VALUES ('old-source','manual_text','旧来源','2026-10-26')")
    c.execute("INSERT INTO raw_item(id,source_id,kind,submission_hash,created_at) VALUES ('old-raw','old-source','manual_text','hash','2026-10-26')")
    c.execute("INSERT INTO raw_snapshot(id,raw_item_id,original_text,created_at,hash) VALUES ('old-snapshot','old-raw','旧原文','2026-10-26','hash')")
    c.execute("INSERT INTO event(id,raw_item_id,raw_snapshot_id,title,created_at,updated_at) VALUES ('old-event','old-raw','old-snapshot','旧事项','2026-10-26','2026-10-26')")
    c.commit()
    before={}
    for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_migrations'"):
        cols=[r[1] for r in c.execute('PRAGMA table_info("'+name+'")')]
        before[name]=(cols,sorted(c.execute('SELECT * FROM "'+name+'"').fetchall(),key=repr))
    c.close()
    value=PersonalStore(path);value.initialize();value.initialize()
    old=value.get_event("old-event")
    assert old["course"] is None
    with value.connection() as c:
        from server.personal_store import SCHEMA_VERSION
        assert c.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]==SCHEMA_VERSION
        assert c.execute("SELECT count(*) FROM course").fetchone()[0]==1
        assert c.execute("SELECT count(*) FROM course_alias").fetchone()[0]==3
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        for name,(cols,rows) in before.items():
            projection=",".join('"'+col+'"' for col in cols)
            assert sorted(map(tuple,c.execute('SELECT '+projection+' FROM "'+name+'"')),key=repr)==rows
    from server.personal_store import SCHEMA_VERSION
    assert list(tmp_path.glob(f"v19.sqlite3.before-v{SCHEMA_VERSION}.backup"))

