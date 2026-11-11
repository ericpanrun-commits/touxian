"""Consistent read models over existing IDs, originals and domain-service owners."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import logging
from contextlib import closing
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

from ..courses import CourseStore
from ..inbox_store import InboxStore
from ..learning.store import LearningStore, unpack, capture_projection
from ..personal_store import PersonalStore
from ..planning.query import PlanningQuery
from ..review_store import ReviewStore
from ..school.artifacts import ReviewArtifacts
from ..school.store import SchoolStore
from ..school.documents import ReadDocuments
from ..school.context import ContextBuilder
from ..school.retrieval import Retrieval
from ..school.service import SchoolService
from ..ai_settings import AISettings
from types import SimpleNamespace
from .pdf_reading import reading_view


PRIVATE_FIELDS = frozenset({
    'local_path', 'canonical_path', 'temporary_path', 'output_path', 'cache_path',
    'preview_path', 'image_path', 'original_path', 'canonical_directory',
    'api_key', 'protected_api_key', 'protected_sync_key', 'sync_key', 'upload_key','protected_worker_key','worker_key',
    'authorization', 'cookie', 'cookies', 'raw_output', 'request_headers',
    'config_json', 'settings_json', 'protected_key', 'key_hash', 'token_hash', 'verifier_hash',
    'path', 'source_path', 'render_path',
})


def public_value(value):
    if isinstance(value, dict):
        result = {}
        for key,item in value.items():
            if key.lower() in PRIVATE_FIELDS: continue
            if key.endswith('_json') and isinstance(item,str):
                try:
                    parsed = json.loads(item)
                    if isinstance(parsed,(dict,list)):item=json.dumps(public_value(parsed),ensure_ascii=False,sort_keys=True)
                except (TypeError,ValueError):pass
            result[key]=public_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [public_value(item) for item in value]
    if isinstance(value, Path):
        raise ValueError('Machine paths must not enter cloud read models')
    return value


def record(kind, record_id, payload):
    payload = public_value(payload)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return {'kind': kind, 'id': str(record_id), 'payload': payload,
            'source_hash': hashlib.sha256(encoded.encode('utf-8')).hexdigest()}


class CloudProjection:
    """Read a backup of the live database; never instantiate mutating services."""
    def __init__(self, source: PersonalStore, cache: Path):
        self.source = source
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.file_hashes = {}
        self.reading_views = {}
        self.reading_failures = set()

    def snapshot(self):
        target = self.cache / 'projection.sqlite3'
        temporary = self.cache / 'projection.next.sqlite3'
        with closing(sqlite3.connect(self.source.path.resolve().as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination)
        temporary.replace(target)
        return PersonalStore(target)

    def records(self):
        personal = self.snapshot()
        school = SchoolStore(personal, None)
        learning = LearningStore(personal)
        reviews = ReviewArtifacts(personal)
        documents=ReadDocuments(personal);retrieval=Retrieval(personal,documents)
        contexts=ContextBuilder(personal,school,documents,retrieval,persist_sources=False)
        settings=AISettings(self.source.path.with_name('ai-settings.json'))
        quote_service=SimpleNamespace(contexts=contexts,extraction=SimpleNamespace(settings=settings),personal=personal)
        result = []
        def add(kind, identity, value):
            result.append(record(kind, identity, value))

        courses = CourseStore(personal).list(limit=1000)
        for course in courses['items']:
            add('course', course['id'], course)
        add('facade', '/courses', courses)
        events = personal.list_events(limit=10000, status='all')
        add('facade', '/events', events)
        for event in events['items']:
            add('event', event['id'], personal.get_event(event['id']))

        with personal.connection() as c:
            for row in InboxStore._rows(c):
                add('inbox_item', row['id'], InboxStore.project(c, row, recovery_database_path=self.source.path))
                add('inbox', row['id'], InboxStore.project_detail(c, row, recovery_database_path=self.source.path))
            for row in c.execute('SELECT * FROM raw_item ORDER BY id'):
                add('raw_item', row['id'], dict(row))
            for row in c.execute('SELECT raw_snapshot_id FROM raw_review ORDER BY raw_snapshot_id').fetchall():
                add('raw_review', row['raw_snapshot_id'], ReviewStore._bundle(c, row['raw_snapshot_id']))
            from ..source_metadata import snapshot_with_metadata
            for row in c.execute('SELECT * FROM raw_snapshot WHERE id NOT IN (SELECT raw_snapshot_id FROM raw_review)'):
                add('snapshot', row['id'], snapshot_with_metadata(c, row))
            for row in c.execute('SELECT * FROM course_alias ORDER BY normalized_alias'):
                add('course_alias', row['normalized_alias'], dict(row))
            for table in ('deadline', 'task', 'reminder', 'learning_followup', 'document',
                          'document_page', 'document_section', 'learning_unit'):
                for row in c.execute('SELECT * FROM '+table+' ORDER BY id'):
                    value=dict(row)
                    if table=='document':
                        attachment=c.execute('SELECT mime_type FROM attachment WHERE id=?',(row['attachment_id'],)).fetchone()
                        if attachment and (attachment['mime_type'] or '').startswith('image/'):
                            value['mime_type']=attachment['mime_type']
                    view=self.reading_views.get(row['id']) if table=='document' else None
                    if view and view['source_sha256']==value['sha256'] and view['page_count']==value['page_count']:
                        add('pdf_reading',view['id'],view)
                        value['reading_resource_id']='pdf-view:'+view['id']
                    add(table, row['id'], value)
            for row in c.execute('SELECT * FROM learning_capture ORDER BY created_at,id'):
                # Picker inventory is read separately. Another lecture changing
                # must not invalidate an offline confirmation of this capture.
                add('learning_capture', row['id'], capture_projection(c,row,include_lecture_choices=False))
            for row in c.execute('SELECT * FROM inbox_receipt ORDER BY created_at,id'):
                # Request IDs map existing Shortcuts receipts to the same domain original.
                add('receipt', row['id'], {key: row[key] for key in ('id','request_id','raw_item_id','created_at')})

        add('facade', '/raw', ReviewStore(personal).list(limit=10000))
        overview = school.overview(limit=20, offset=0)
        add('facade', '/school', overview)
        add('facade', '/school/textbook', school.book())
        add('facade', '/school/notes', {'items': school.note_choices()})
        for course in courses['items']:
            suffix='?course_id='+course['id']
            add('facade','/school'+suffix,school.overview(limit=20,offset=0,course_id=course['id']))
            add('facade','/school/notes'+suffix,{'items':school.note_choices(course['id'])})
        for book in school.books():
            add('textbook',book['id'],book)
        with personal.connection() as c:
            lecture_ids = [row[0] for row in c.execute('SELECT id FROM lecture ORDER BY lecture_date,id')]
            review_ids = [row[0] for row in c.execute('SELECT id FROM review_artifact ORDER BY created_at,id')]
            topic_ids = [row[0] for row in c.execute('SELECT id FROM learning_topic ORDER BY created_at,id')]
        for identity in lecture_ids:
            value = school.lecture(identity)
            add('lecture', identity, value)
            path='/school/lectures/'+identity
            add('facade',path+'/context',contexts.build(identity,allow_incomplete=True))
            add('facade',path+'/cost',SchoolService.quote_cost(quote_service,identity))
            add('facade',path+'/section-suggestions',{'items':retrieval.suggest(value),'automatic_confirmation':False})
            scope={}
            for doc in value['notes']:
                for page in range(1,(doc.get('page_count') or 0)+1):scope[(doc['id'],page)]=('notes',None,None)
            for section in retrieval.sections(identity):
                for page in range(section['start_page'],section['end_page']+1):scope[(section['document_id'],page)]=('textbook',section['title'],section['textbook_title'])
            for (doc_id,page),(role,section,book_title) in scope.items():
                source=contexts.image_source(documents.get(doc_id),page,role,title=book_title,section=section)
                add('facade',path+'/page-source?document_id='+doc_id+'&page='+str(page),source)
            for job in value['jobs']:
                add('facade','/school/jobs/'+job['id'],{**job,'cost':SchoolService.cost(quote_service,job['id'])})
            add('lecture_item', identity, {key: value[key] for key in ('id','course_id','lecture_date','title','status','version','updated_at','accepted_artifact_id')})
            add('recent_item', 'lecture:'+identity, {'id': identity,'kind':'lecture','title':value['title'],'course_id':value['course_id'],
                'occurred_at':value['updated_at'],'status':value['status']})
        for identity in review_ids:
            value = reviews.get(identity)
            lecture = school.lecture(value['lecture_id'])
            add('review', identity, value)
            add('review_item', identity, {**{key: value[key] for key in ('id','lecture_id','status','version','created_at','generated_at','adopted')},
                'has_content':bool(value['content']),'title':lecture['title'],'course_id':lecture['course_id'],'lecture_date':lecture['lecture_date']})
            add('recent_item', 'review:'+identity, {'id':identity,'kind':'review','title':lecture['title']+' · 复盘','course_id':lecture['course_id'],
                'occurred_at':value['generated_at'] or value['created_at'],'status':value['status']})
        for identity in topic_ids:
            topic=learning.topic(identity)
            for artifact in topic['artifacts']:
                if artifact['kind']!='retro_summary':
                    payload=artifact.get('payload') or {}
                    artifact['payload']={k:payload[k] for k in ['category','reason','uncertain','origin','attempt_id'] if k in payload}
            add('topic', identity, topic)
        from ..learning.analysis import authorization
        from ..learning.auto_notes import vision_authorization
        with personal.connection() as c:
            background=authorization(c)
            browser=c.execute("SELECT * FROM learning_artifact WHERE kind='browser_batch' ORDER BY rowid DESC LIMIT 1").fetchone()
            followups=[r[0] for r in c.execute("SELECT f.id FROM learning_followup f JOIN learning_exercise e ON e.id=f.exercise_id WHERE f.status='ready' AND NOT EXISTS(SELECT 1 FROM learning_attempt a WHERE a.exercise_id=e.id)")]
        from ..learning.service import LearningService
        reader=SimpleNamespace(personal=personal,store=learning)
        for identity in followups:add('facade','/learning/retests/'+identity,LearningService.quiz(reader,identity))
        browser_value=unpack(browser,'payload_json','error_json') if browser else None
        add('facade', '/learning', {**learning.overview(),'background_analysis':{**background,'available':settings.public()['has_api_key']},
            'notes_vision':vision_authorization(personal),'browser_sync':{'status':browser['status'] if browser else 'idle','busy':bool(browser and browser['status']=='running'),
                'message':(browser_value['payload'] or {}).get('message') if browser_value else 'Windows 上线后可读取已登录的学习通页面。'}})
        # Keep structured facts; cloud Today recomputes the current Shanghai day.
        add('facade', '/planning/facts', {'items': PlanningQuery(personal).deadlines(include_terminal=True)})
        from ..learning.note_course import timetable
        schedule = timetable(self.source.path.with_name('notes-timetable.json'))
        if schedule:
            add('facade', '/timetable', CourseStore(personal).timetable(schedule))
        for item in list(result):
            if item['kind']=='inbox_item' and item['payload'].get('managed'):
                value=item['payload']
                add('recent_item','inbox:'+item['id'],{'id':item['id'],'kind':'inbox','title':value['title'],
                    'occurred_at':value['created_at'],'status':value['processing_status']})
        # Publish this atomic membership record after every referenced review
        # and summary. Sequential worker batches cannot expose a new queue ID
        # before its original record has arrived.
        add('facade', '/review-queue', reviews.daily_queue())
        return result

    def resources(self):
        """Private worker manifest. Absolute source paths never enter public records."""
        resources = []
        with self.source.connection() as c:
            rows = [dict(row) for row in c.execute('SELECT * FROM attachment WHERE local_path IS NOT NULL ORDER BY id')]
            documents = [dict(row) for row in c.execute('SELECT * FROM document ORDER BY id')]
        by_attachment = {row['id']: row for row in rows}
        for attachment in rows:
            path = Path(attachment['local_path']).resolve()
            if not path.is_relative_to((self.source.path.parent/'files').resolve()):
                from ..school.library import registered_path
                path = registered_path(self.source, attachment, path)
            if not path.is_file():
                raise FileNotFoundError('An existing original is unavailable; do not publish a false file receipt')
            sha = self._hash(path)
            if attachment.get('sha256') and sha != attachment['sha256']:
                raise ValueError('An existing original failed its content-hash verification')
            resources.append({'id': 'attachment:'+attachment['id']+':original', 'filename': attachment['filename'],
                'mime': attachment.get('mime_type') or mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                'sha256': sha, 'bytes': path.stat().st_size, 'path': path,
                'attachment_id': attachment['id']})
        files={resource['attachment_id']:resource for resource in resources}
        documents=[{**doc,'path':files[doc['attachment_id']]['path']} for doc in documents if doc['attachment_id'] in files]
        views={}
        for doc in documents:
            if files[doc['attachment_id']]['mime']!='application/pdf' or (doc.get('page_count') or 0)<64:continue
            try:
                if doc['sha256']!=files[doc['attachment_id']]['sha256']:raise ValueError('Document original mismatch')
                view=reading_view(doc,self.cache/'pdf-reading')
                if view:views[doc['id']]=view
            except Exception:
                # A derived reading optimization must never stop reception or
                # canonical processing. The immutable original stays readable.
                if doc['sha256'] not in self.reading_failures:
                    logging.getLogger(__name__).warning('PDF reading index unavailable; original retained (%s)',doc['id'])
                    self.reading_failures.add(doc['sha256'])
        self.reading_views=views
        return resources,documents

    def _hash(self, path):
        stat = path.stat()
        fingerprint = (str(path), stat.st_size, stat.st_mtime_ns)
        if fingerprint not in self.file_hashes:
            with path.open('rb') as stream:
                self.file_hashes[fingerprint] = hashlib.file_digest(stream, 'sha256').hexdigest()
        return self.file_hashes[fingerprint]
