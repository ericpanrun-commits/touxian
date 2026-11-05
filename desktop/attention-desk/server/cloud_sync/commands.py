"""Typed domain execution with a receipt in the same transaction as pure local edits."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import re

from ..personal_store import PersonalStore, PersonalError, encode, now
from ..personal_models import EventEditInput, StatusInput, MutationInput, TaskInput, TaskEditInput, DeadlineInput
from ..course_api import CourseInput, CourseAssociationInput
from ..courses import CourseStore
from ..inbox_api import LinkInput
from ..inbox_store import InboxStore
from ..school.artifacts import ReviewArtifacts
from ..school.models import FeedbackInput, LectureInput, SectionLinks, BookMetadataInput, BookFileInput, SectionInput
from ..school.store import SchoolStore
from ..review_store import ReviewStore
from ..extraction_models import ConfirmationInput,DraftInput


class TransactionView(PersonalStore):
    """An explicit scoped store; normal app connections and background jobs are untouched."""
    def __init__(self, personal, connection):
        super().__init__(personal.path)
        self.transaction = connection

    @contextmanager
    def connection(self, *, write=False, migration=False):
        if migration or not self.transaction.in_transaction:
            raise RuntimeError('Cloud commands cannot migrate or escape their transaction')
        yield self.transaction


def execute_atomic(personal, command, action):
    signature=hashlib.sha256(encode({'method':command['method'],'path':command['path'],'body':command['body']}).encode()).hexdigest()
    with personal.connection(write=True) as c:
        receipt=c.execute('SELECT * FROM cloud_command_receipt WHERE id=?',(command['id'],)).fetchone()
        if receipt:
            if receipt['request_hash']!=signature:
                raise PersonalError('同一操作标识对应了不同内容，原有修改保留。','idempotency_conflict',409)
            return json.loads(receipt['result_json'])
        result=action(TransactionView(personal,c))
        c.execute('INSERT INTO cloud_command_receipt VALUES(?,?,?,?)',(command['id'],signature,encode(result),now()))
        return result


def stored_atomic_result(personal,command):
    """Read a completed local outcome before repeating file preparation."""
    signature=hashlib.sha256(encode({'method':command['method'],'path':command['path'],'body':command['body']}).encode()).hexdigest()
    with personal.connection() as c:
        receipt=c.execute('SELECT * FROM cloud_command_receipt WHERE id=?',(command['id'],)).fetchone()
    if not receipt:return None
    if receipt['request_hash']!=signature:
        raise PersonalError('同一操作标识对应了不同内容，原有修改保留。','idempotency_conflict',409)
    return json.loads(receipt['result_json'])


class CommandExecutor:
    def __init__(self, personal,school,extraction,intake,learning=None):
        self.personal,self.school,self.extraction,self.intake,self.learning=personal,school,extraction,intake,learning

    def pure(self, command):
        method,path,body=command['method'],command['path'],command['body']
        def dispatch(store):
            body=command['body']
            if path.startswith('/events/') and isinstance(body.get('expected_version'),int):
                from .progress import rebase_task_versions
                event_id=path.split('/')[2]
                current=store.transaction.execute('SELECT version FROM event WHERE id=?',(event_id,)).fetchone()
                if current and current['version']!=body['expected_version']:
                    rebased=rebase_task_versions(store.transaction,event_id,body['expected_version'],current['version'])
                    if rebased is not None:body={**command['body'],'expected_version':rebased}
                    else:body=command['body']
                else:body=command['body']
            else:body=command['body']
            if path.startswith('/learning/'):
                from ..learning.store import LearningStore
                from ..learning.models import HistoryInput,ExternalReviewInput,AnalysisAuthorizationInput
                learning=LearningStore(store)
                if path=='/learning/history/confirm' and method=='POST':
                    p=HistoryInput.model_validate(body);return learning.confirm_history(p.topic_ids,p.learned)
                if path=='/learning/external-reviews' and method=='POST':
                    from ..school.documents import digest
                    p=ExternalReviewInput.model_validate(body)
                    artifact=learning.reserve_artifact('external_review',digest(p.text))
                    if artifact['status']=='running':learning.finish_artifact(artifact['id'],{'text':p.text,'role':'External Review','applied_to_learning_data':False})
                    return {'saved':True,'id':artifact['id'],'applied_to_learning_data':False}
                if path=='/learning/analysis/authorization' and method=='POST':
                    from ..learning.analysis import record_authorization
                    return record_authorization(learning,AnalysisAuthorizationInput.model_validate(body))
            match=re.fullmatch(r'/raw/([^/]+)/(confirm|draft|cancel|resume)',path)
            if match:
                snapshot,operation=match.groups();reviews=ReviewStore(store)
                if operation=='confirm' and method=='POST':return reviews.confirm(snapshot,ConfirmationInput.model_validate(body))
                if operation=='draft' and method=='PUT':
                    p=DraftInput.model_validate(body);return reviews.save_draft(snapshot,p.expected_version,p.draft)
                if operation in ('cancel','resume') and method=='POST':return reviews.set_status(snapshot,MutationInput.model_validate(body).expected_version,'cancelled' if operation=='cancel' else 'pending')
            match=re.fullmatch(r'/inbox/([^/]+)/links',path)
            if match and method=='PUT':
                p=LinkInput.model_validate(body)
                return InboxStore(store).link(match[1],p.expected_version,p.course_id,p.project_ref)
            match=re.fullmatch(r'/school/reviews/([^/]+)/feedback',path)
            if match and method=='POST':
                return ReviewArtifacts(store).feedback(match[1],FeedbackInput.model_validate(body))
            match=re.fullmatch(r'/school/reviews/([^/]+)/source-correction',path)
            if match and method=='POST':
                from ..school.models import SourceCorrectionInput
                from ..school.source_correction import save_correction
                return save_correction(store,match[1],SourceCorrectionInput.model_validate(body))
            match=re.fullmatch(r'/school/lectures/([^/]+)/sections',path)
            if match and method=='PUT':
                p=SectionLinks.model_validate(body)
                return SchoolStore(store,None).link_sections(match[1],p.expected_version,p.section_ids)
            if path=='/courses' and method=='POST':
                p=CourseInput.model_validate(body)
                return CourseStore(store).create(p.name)
            if path=='/school/textbooks' and method=='POST':
                return SchoolStore(store,None).register_book(BookMetadataInput.model_validate(body),command['id'])
            if path=='/school/sections' and method=='POST':
                p=SectionInput.model_validate(body)
                if p.expected_version is None:
                    raise PersonalError('请重新读取教材后选择章节。','missing_version',409)
                return SchoolStore(store,None).add_section(p)
            match=re.fullmatch(r'/events/([^/]+)(?:/(status|restore|course|tasks|deadlines)(?:/([^/]+))?)?',path)
            if match:
                event,operation,child=match.groups()
                if not operation and method=='PATCH':
                    p=EventEditInput.model_validate(body)
                    return store.edit_event(event,p.expected_version,p.model_dump(exclude={'expected_version'}))
                if operation=='status' and method=='POST':
                    p=StatusInput.model_validate(body)
                    return store.change_status(event,p.expected_version,p.status,p.confirm_incomplete)
                if operation=='restore' and method=='POST':
                    return store.restore_event(event,MutationInput.model_validate(body).expected_version)
                if operation=='course' and method=='PUT':
                    p=CourseAssociationInput.model_validate(body)
                    return store.set_course(event,p.expected_version,p.course_id)
                if operation=='tasks' and not child and method=='POST':
                    return store.add_task(event,TaskInput.model_validate(body))
                if operation=='tasks' and child and method=='PATCH':
                    p=TaskEditInput.model_validate(body)
                    return store.edit_task(event,child,p.expected_version,p.model_dump(exclude={'expected_version'},exclude_unset=True))
                if operation=='deadlines' and not child and method=='POST':
                    return store.add_deadline(event,DeadlineInput.model_validate(body))
                if operation=='deadlines' and child and method=='PUT':
                    return store.edit_deadline(event,child,DeadlineInput.model_validate(body))
            raise PersonalError('此操作尚未接通，请保留当前内容。','cloud_action_unavailable',400)
        return execute_atomic(self.personal,command,dispatch)

    async def execute(self,command):
        import asyncio
        path,method,body=command['path'],command['method'],command['body']
        if method=='PUT' and re.fullmatch(r'/inbox/([^/]+)/links',path):
            result=await asyncio.to_thread(self.pure,command)
            if self.learning is not None:self.learning.resume_pending_note_links()
            return result
        if method=='POST' and self.learning is not None:
            if path=='/learning/answers':
                from ..learning.api import answers
                from ..learning.models import AnswerInput
                return await answers(AnswerInput.model_validate({**body,'request_id':command['id']}),self.learning)
            if path=='/learning/sync':
                from ..learning.browser_batch import BrowserBatch
                return BrowserBatch(self.learning).start(command['id'])
            if path=='/learning/history/confirm':
                result=await asyncio.to_thread(self.pure,command)
                if result['status']=='confirmed':
                    for tid in result['topic_ids']:self.learning.schedule('history:'+tid,lambda tid=tid:self.learning.prepare_history(tid))
                return result
            if path=='/learning/analysis/authorization':
                result=await asyncio.to_thread(self.pure,command)
                if result['enabled']:self.learning.resume_background()
                return result
        match=re.fullmatch(r'/school/reviews/([^/]+)/proposals',path)
        if match and method=='POST':
            from ..school.proposals import stage_proposals
            from ..school.models import VersionInput
            return await asyncio.to_thread(stage_proposals,self.school,match[1],VersionInput.model_validate(body).expected_version)
        match=re.fullmatch(r'/school/lectures/([^/]+)/(generate|vision)',path)
        if match and method=='POST':
            from ..school.models import GenerateInput,VisionInput
            # Canonical paid-operation ledgers already reserve this request before scheduling.
            payload={**body,'request_id':command['id']}
            return await (self.school.generate(match[1],GenerateInput.model_validate(payload)) if match[2]=='generate'
                          else self.school.start_vision(match[1],VisionInput.model_validate(payload)))
        if path=='/school/lectures' and method=='POST':
            return await asyncio.to_thread(self.school.store.create_lecture,LectureInput.model_validate(body),command['id'])
        if path=='/school/textbook/file' and method=='PUT':
            p=BookFileInput.model_validate(body)
            def commit_file():
                old=stored_atomic_result(self.personal,command)
                if old is not None:return old
                source=self.school.store.prepare_book_file(p.raw_item_id,p.textbook_id)
                return execute_atomic(self.personal,command,lambda view:SchoolStore(view,None).link_registered_book(
                    p.raw_item_id,p.expected_version,p.file_role,**source,textbook_id=p.textbook_id))
            book=await asyncio.to_thread(commit_file)
            job=self.school.start_catalog(book['document_id'],command['id'])
            return {'textbook':book,'job':job}
        match=re.fullmatch(r'/school/sections/([^/]+)/index',path)
        if match and method=='POST':
            from ..school.api import JobRequest
            p=JobRequest.model_validate({**body,'request_id':command['id']})
            return self.school.start_index(match[1],p.request_id,retry=p.retry)
        match=re.fullmatch(r'/school/documents/([^/]+)/catalog',path)
        if match and method=='POST':
            from ..school.api import JobRequest
            p=JobRequest.model_validate({**body,'request_id':command['id']})
            return self.school.start_catalog(match[1],p.request_id,retry=p.retry)
        match=re.fullmatch(r'/inbox/([^/]+)/retry',path)
        if match and method=='POST':
            return await self.intake.retry(match[1],body['expected_version'])
        match=re.fullmatch(r'/learning/captures/([^/]+)/confirm',path)
        if match and method=='POST' and self.learning is not None:
            from ..learning.api import ConfirmCapture
            p=ConfirmCapture.model_validate(body)
            return await self.learning.confirm_capture(match[1],p.course_id,lecture_id=p.lecture_id,lecture_date=p.lecture_date)
        return await asyncio.to_thread(self.pure,command)
