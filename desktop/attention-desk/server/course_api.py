"""Desktop course catalogue; formal associations keep event version/audit rules."""
from fastapi import APIRouter, Depends, Query
from pydantic import Field
from .personal_api import perform, store_for
from .personal_models import InputModel, MutationInput
from .courses import CourseStore

router = APIRouter(prefix="/api/personal", tags=["courses"])

class CourseInput(InputModel):
    name: str = Field(min_length=1, max_length=120)

class CourseAssociationInput(MutationInput):
    course_id: str | None = Field(default=None, min_length=1, max_length=36)

@router.get("/courses")
def list_courses(limit:int=Query(100,ge=1,le=100), offset:int=Query(0,ge=0), store=Depends(store_for)):
    return perform(lambda:CourseStore(store).list(limit,offset))

@router.post("/courses", status_code=201)
def create_course(payload:CourseInput,store=Depends(store_for)):
    return perform(lambda:CourseStore(store).create(payload.name))

@router.get("/courses/resolve")
def resolve_course(name:str=Query(min_length=1,max_length=120),store=Depends(store_for)):
    return perform(lambda:{"course":CourseStore(store).find(name)})

@router.get("/courses/{course_id}")
def get_course(course_id:str,store=Depends(store_for)):
    return perform(lambda:CourseStore(store).get(course_id))

@router.put("/events/{event_id}/course")
def associate_course(event_id:str,payload:CourseAssociationInput,store=Depends(store_for)):
    return perform(lambda:store.set_course(event_id,payload.expected_version,payload.course_id))

