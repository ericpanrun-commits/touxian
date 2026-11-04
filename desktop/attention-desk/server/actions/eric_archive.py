"""Raw-only projection from the existing official store into the existing Eric Inbox.

No crawler, notification dispatcher, model calls, publisher epoch or domain commands.
Existing Eric identities (including tombstones) and user state always win.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID, NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from ..official_sources import url_identity
from .database import PostgresDatabase
from .runtime import ActionsRuntime


ARCHIVER = 'zqu-actions-eric-v1'
# The approved workspace/owner pair, bound without publishing private account IDs.
TARGET_BINDING_SHA256 = 'e4908b359d8b47d0ab0344a985218524d74e6953ac6e461278ecc5ca1080db31'
DEFAULT_ARCHIVE_ENABLED = False
KINDS = ('raw_item', 'snapshot', 'inbox', 'inbox_item')
READ_ITEMS = '''SELECT i.id,i.canonical_url,i.body,
  coalesce((SELECT jsonb_agg(jsonb_build_object('source_id',r.source_id,
    'name',s.config->>'name','first_seen_at',r.first_seen_at) ORDER BY r.first_seen_at,r.source_id)
    FROM zqu_worker.source_refs r JOIN zqu_worker.sources s ON s.id=r.source_id
    WHERE r.item_id=i.id),'[]'::jsonb) sources
  FROM zqu_worker.items i
  WHERE NOT EXISTS (SELECT 1 FROM public.eric_app_records r
    WHERE r.workspace_id=%s AND r.kind='raw_item'
      AND (r.id=i.id OR r.payload->>'canonical_url'=i.canonical_url))
    AND nullif(i.body->>'fetched_at','') IS NOT NULL
    AND (coalesce(i.body->>'original_text','')<>'' OR i.body->>'body_status' IN ('image_only','document_only'))
    AND (%s::text[] IS NULL OR i.id=ANY(%s::text[]))
  ORDER BY i.body->>'discovered_at',i.id LIMIT %s'''


@dataclass(frozen=True)
class ArchiveTarget:
    workspace: str
    owner: str
    database_id: str

    def __post_init__(self):
        for value in (self.workspace, self.owner):
            if str(UUID(value)) != value:
                raise ValueError('Explicit canonical workspace and owner IDs are required.')
        if not self.database_id or self.database_id != self.database_id.strip() or len(self.database_id) > 128:
            raise ValueError('The existing opaque official database ID is required.')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def record(kind, identity, payload):
    return {'kind': kind, 'id': identity, 'payload': payload,
            'source_hash': hashlib.sha256(encoded(payload).encode()).hexdigest()}


def project(item):
    """Keep the source ID and immutable text; never infer courses or formal dates."""
    body, identity = item['body'], item['id']
    sources = item['sources']
    if not sources or not body.get('fetched_at'):
        return []  # Discovery/fetching is not a saved page; retry next existing cycle.
    text = body.get('original_text') or ''
    if not text and body.get('body_status') not in ('image_only', 'document_only'):
        return []
    url = item['canonical_url']
    source = sources[0]
    created = body.get('discovered_at') or str(source['first_seen_at'])
    stamp = body['fetched_at']
    digest = hashlib.sha256(text.encode()).hexdigest()
    snapshot_id = str(uuid5(NAMESPACE_URL, f'{ARCHIVER}:{identity}:{body.get("version_hash") or digest}'))
    snapshot = {'id': snapshot_id, 'raw_item_id': identity, 'created_at': stamp,
        'source_time': body.get('published_at'), 'source_timezone': 'Asia/Shanghai',
        'original_text': text, 'hash': digest, 'blocks': [], 'attachments': [],
        'web': {'version': 1, 'raw_snapshot_id': snapshot_id, 'source_id': source['source_id'],
            'page_title': body.get('title') or '', 'title': body.get('title') or '',
            'published_at': body.get('published_at'), 'fetched_at': stamp,
            'original_url': body.get('url') or url, 'canonical_url': url,
            'version_hash': body.get('version_hash'), 'body_status': body.get('body_status'),
            'images': body.get('images') or [], 'attachments': body.get('attachments') or []}}
    parsed_url = urlsplit(url_identity(url))
    raw = {'id': identity, 'kind': 'official_web', 'source_type': 'official_web',
        'source_id': source['source_id'], 'external_id': parsed_url.netloc + ':' + parsed_url.path,
        'title': body.get('title') or '', 'original_url': body.get('url') or url,
        'canonical_url': url, 'published_at': body.get('published_at'), 'discovered_at': created,
        'created_at': created, 'inbox_kind': None, 'inbox_version': 1,
        'inbox_course_id': None, 'inbox_status': None, 'official_backfill': body.get('official_backfill', 0),
        'eric_archive': ARCHIVER, 'source_refs': sources}
    # Completed means the original was saved. It does not mean read, adopted or confirmed.
    classification = {key: body.get(key) for key in ('category', 'importance', 'relevance', 'rule_version', 'reasons')}
    classification['archive_only'] = True
    card = {'id': identity, 'title': raw['title'], 'created_at': created,
        'source': source['name'] or source['source_id'], 'source_kind': 'official_web',
        'input_kind': 'url', 'preview': text[:240], 'preview_source': 'text',
        'processing_status': 'completed', 'processing_stage': 'legacy_saved',
        'original_snapshot_id': snapshot_id, 'snapshot_id': snapshot_id,
        'course': None, 'project_ref': None, 'classification': classification, 'error': None,
        'proposal_count': 0, 'review_status': None, 'version': 1, 'attachments': [],
        'managed': False, 'needs_manual': False}
    detail = {**card, 'original': snapshot, 'review_available': False,
        'review': {'snapshot': snapshot, 'extraction': None, 'course_suggestions': {}},
        'extraction_quality': {'files': [], 'reasons': [], 'needs_review': False},
        'school_documents': [], 'receipts': []}
    return [record('raw_item', identity, raw), record('snapshot', snapshot_id, snapshot),
            record('inbox', identity, detail), record('inbox_item', identity, card)]


def verify_target(db, target):
    row = db.execute('''SELECT m.database_id,m.schema_version,m.import_completed_at,m.activation_at,
        w.owner_id,(has_table_privilege(current_user,'public.eric_app_records','SELECT')
          AND has_table_privilege(current_user,'public.eric_app_records','INSERT')
          AND has_table_privilege(current_user,'public.eric_app_changes','INSERT')
          AND has_sequence_privilege(current_user,'public.eric_app_revision_seq','USAGE')) allowed
        FROM zqu_worker.meta m JOIN public.eric_app_workspaces w ON w.id=%s WHERE m.id=1''',
        (target.workspace,)).fetchone()
    if not row or str(row['database_id']) != target.database_id or row['schema_version'] != 1 \
            or not row['import_completed_at'] or not row['activation_at'] \
            or str(row['owner_id']) != target.owner or not row['allowed']:
        raise ValueError('Official store identity, Eric owner or existing write permissions do not match.')


def resolve_target(database, database_id, *, workspace=None, owner=None, expected_binding=TARGET_BINDING_SHA256):
    if bool(workspace) != bool(owner):
        raise ValueError('Workspace and owner must be configured together.')
    with database.connection() as db:
        db.execute('SET TRANSACTION READ ONLY')
        rows = db.execute('SELECT id,owner_id FROM public.eric_app_workspaces').fetchall()
    matches = [row for row in rows if hashlib.sha256(f"{row['id']}:{row['owner_id']}".encode()).hexdigest() == expected_binding
               and (not workspace or str(row['id']) == workspace and str(row['owner_id']) == owner)]
    if len(matches) != 1:
        raise ValueError('The explicitly approved workspace/owner binding is unavailable.')
    return ArchiveTarget(str(matches[0]['id']), str(matches[0]['owner_id']), database_id)


def sync(database, target, *, apply=False, runtime=None, limit=1000, approved_ids=None):
    if not 1 <= limit <= 1000:
        raise ValueError('Archive batch must be bounded.')
    if approved_ids is not None:
        if not isinstance(approved_ids, (list, tuple, set, frozenset)) or len(approved_ids) > 1000 \
                or any(not isinstance(identity, str) or not identity for identity in approved_ids):
            raise ValueError('Approved source IDs must be an explicit bounded list.')
        approved_ids = frozenset(approved_ids)
    if apply and (runtime is None or not runtime.execution_verified
                  or runtime.environment != 'production' or not runtime.auto_sync):
        raise ValueError('Applying requires the existing protected production Actions context.')
    result = {'mode': 'apply' if apply else 'read_only', 'candidates': 0, 'ready': 0,
              'deferred': 0, 'inserted_items': 0, 'inserted_records': 0, 'duplicate_prevented': 0}
    with database.connection() as db:
        db.execute('SET TRANSACTION READ ONLY')
        verify_target(db, target)
        identities = sorted(approved_ids) if approved_ids is not None else None
        items = db.execute(READ_ITEMS, (target.workspace, identities, identities, limit)).fetchall()
    if approved_ids is not None:
        items = [item for item in items if item['id'] in approved_ids]
    result['candidates'] = len(items)
    for item in items:
        records = project(item)
        if not records:
            result['deferred'] += 1
            continue
        result['ready'] += 1
        if not apply:
            continue
        with database.connection() as db:
            # Share the established transaction lock, never its publisher epoch/heartbeat.
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended('eric-app:'||%s,0))", (target.workspace,))
            verify_target(db, target)
            if db.execute('''SELECT 1 FROM public.eric_app_records WHERE workspace_id=%s AND kind='raw_item'
                AND (id=%s OR payload->>'canonical_url'=%s)''',
                (target.workspace, item['id'], item['canonical_url'])).fetchone():
                result['duplicate_prevented'] += 1
                continue
            for value in records:
                inserted = db.execute('''INSERT INTO public.eric_app_records
                    (workspace_id,kind,id,payload,source_hash,source_epoch,source_generation)
                    VALUES(%s,%s,%s,%s,%s,NULL,0) ON CONFLICT(workspace_id,kind,id) DO NOTHING
                    RETURNING id''', (target.workspace, value['kind'], value['id'],
                    Jsonb(value['payload']), value['source_hash'])).fetchone()
                if not inserted:
                    # A partial/mismatched existing projection must never become a mixed original.
                    raise ValueError('Conflicting existing projection; this item transaction was rolled back.')
            # The established record trigger creates revisions/change-feed entries atomically.
        result['inserted_items'] += 1
        result['inserted_records'] += len(records)
    return result


def main():
    database = None
    try:
        env = dict(os.environ)
        runtime = ActionsRuntime.from_env(env)
        database = PostgresDatabase(runtime.database_url)
        target = resolve_target(database, env['PRODUCTION_DATABASE_ID'], workspace=env.get('ERIC_OFFICIAL_WORKSPACE_ID'), owner=env.get('ERIC_OFFICIAL_OWNER_ID'))
        approved = json.loads(env['ERIC_OFFICIAL_ARCHIVE_APPROVED_IDS']) if env.get('ERIC_OFFICIAL_ARCHIVE_APPROVED_IDS') else None
        enabled = env.get('ERIC_OFFICIAL_ARCHIVE_ENABLED')
        result = sync(database, target, apply=enabled == 'true' if enabled else DEFAULT_ARCHIVE_ENABLED,
                      runtime=runtime, approved_ids=approved)
        print(json.dumps(result, sort_keys=True))  # Counts only: no DSN, title, source text or user data.
        return 0
    except Exception:
        print(json.dumps({'mode': 'failed', 'code': 'official_archive_configuration_or_database'}))
        return 1
    finally:
        if database:
            database.close()


if __name__ == '__main__':
    raise SystemExit(main())
