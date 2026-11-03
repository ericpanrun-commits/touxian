"""Explicit synthetic course acceptance fixture; never imported by production."""
import asyncio
from datetime import datetime,timedelta
from uuid import uuid5,NAMESPACE_URL
from zoneinfo import ZoneInfo
from fastapi import Body
from server.courses import CourseStore
from server.review_store import ReviewStore
from server.extraction_models import RawInput
from server.extraction_service import ExtractionService
from server.test_extraction_review import candidate,FixtureProvider,TestSettings,finish

def install(app):
    @app.post("/__course_fixture/seed")
    async def seed(payload:dict=Body(default={})):
        if payload.get("kind","math") not in {"math","plain"}:
            from fastapi import HTTPException
            raise HTTPException(400)
        kind=payload.get("kind","math")
        date=datetime.now(ZoneInfo("Asia/Shanghai")).date()
        phrase=f"{date.year}年{date.month}月{date.day}日前"
        course="高等数学 II" if kind=="math" else "实验室安全教育"
        text=f"【{course} · 隔离验证样例】请于{phrase}完成练习并提交作业。"
        title="提交高数作业（隔离验证样例）" if kind=="math" else "提交安全教育记录（隔离验证样例）"
        store=app.state.personal_store
        reviews=ReviewStore(store)
        raw=reviews.collect(RawInput(original_text=text,title_hint=title,source_time=(date-timedelta(days=1)).isoformat()+" 10:00"),
                            str(uuid5(NAMESPACE_URL,"eric-os-phase1-course-fixture/"+kind+"/"+date.isoformat())))
        if raw["extraction"] is None:
            output=candidate(text,dates=[(phrase,date.isoformat())],actions=["完成练习并提交作业"],title=title,category="课程作业" if kind=="math" else "其他")
            service=ExtractionService(store,TestSettings(),FixtureProvider(output))
            await service.start(raw["snapshot"]["id"])
            await finish(service)
            await service.close()
        bundle=reviews.get(raw["snapshot"]["id"])
        CourseStore(store).create("大学物理")
        return {"snapshot_id":bundle["snapshot"]["id"],"version":bundle["version"],
                "suggestion":bundle["course_suggestions"].get("event1"),
                "course":"高等数学 II" if kind=="math" else None,
                "synthetic":True,"production_data_written":False}

