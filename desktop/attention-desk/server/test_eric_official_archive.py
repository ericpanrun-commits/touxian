"""Archive behavior tests use isolated state; never connect to a remote database."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest

from server.actions.eric_archive import ARCHIVER, ArchiveTarget, KINDS, project, resolve_target, sync

WORKSPACE = '11111111-1111-4111-8111-111111111111'
OWNER = '22222222-2222-4222-8222-222222222222'
TARGET = ArchiveTarget(WORKSPACE, OWNER, 'existing-opaque-database-id')
RUNTIME = SimpleNamespace(execution_verified=True, environment='production', auto_sync=True)


def item(identity='existing-source-id', **body):
    return {'id': identity, 'canonical_url': f'https://www.zqu.edu.cn/info/1/{identity}.htm',
        'body': {'title': '官网测试原件', 'original_text': '原文\n不生成事项', 'body_status': 'text',
                 'published_at': '2026-11-03', 'discovered_at': '2026-11-04T08:09:00Z',
                 'fetched_at': '2026-11-04T08:10:00Z', **body},
        'sources': [{'source_id': 'zqu-existing-source', 'name': '已配置来源',
                     'first_seen_at': '2026-11-04T08:09:00Z'}]}


class FakeDatabase:
    """Transaction rollback/atomicity model; SQL syntax is checked separately."""
    def __init__(self, items):
        self.items = items
        self.rows = {}
        self.calls = []
        self.fail_kind = None
        self.owner = OWNER
        self.allowed = True

    @contextmanager
    def connection(self):
        before = deepcopy(self.rows)
        try:
            yield self
        except Exception:
            self.rows = before
            raise

    def execute(self, sql, args=()):
        self.calls.append((sql, args))
        result = []
        if sql == 'SELECT id,owner_id FROM public.eric_app_workspaces':
            result = [{'id': WORKSPACE, 'owner_id': self.owner}]
        elif 'FROM zqu_worker.meta' in sql:
            result = [{'database_id': TARGET.database_id, 'schema_version': 1,
                       'import_completed_at': True, 'activation_at': True,
                       'owner_id': self.owner, 'allowed': self.allowed}]
        elif 'FROM zqu_worker.items i' in sql:
            result = self.items  # Includes races/duplicates deliberately; the second check owns correctness.
        elif sql.startswith('SELECT 1 FROM public.eric_app_records'):
            result = [r for (kind, identity), r in self.rows.items() if kind == 'raw_item'
                      and (identity == args[1] or r.get('canonical_url') == args[2])]
        elif sql.startswith('INSERT INTO public.eric_app_records'):
            workspace, kind, identity, payload, digest = args
            assert workspace == WORKSPACE
            assert kind in KINDS
            if self.fail_kind == kind:
                raise RuntimeError('isolated interrupted transaction')
            if (kind, identity) not in self.rows:
                self.rows[kind, identity] = payload.obj
                result = [{'id': identity}]
        return SimpleNamespace(fetchone=lambda: result[0] if result else None, fetchall=lambda: result)


@pytest.mark.parametrize('push_status', ['suppressed', 'sent', 'pending', 'failed', None])
def test_bark_status_does_not_control_original_archive(push_status):
    source = item(notification_status=push_status)
    before = deepcopy(source)
    records = project(source)
    assert [r['kind'] for r in records] == list(KINDS)
    assert records[0]['id'] == source['id']
    assert source == before
    detail = next(r['payload'] for r in records if r['kind'] == 'inbox')
    assert detail['original']['original_text'] == source['body']['original_text']
    assert detail['original']['source_time'] == '2026-11-03'
    assert detail['original']['web']['source_id'] == 'zqu-existing-source'
    assert not detail['review_available'] and not detail['managed']
    assert detail['proposal_count'] == 0 and detail['course'] is None


def test_repeated_source_id_and_canonical_alias_preserve_every_existing_state():
    source = item()
    database = FakeDatabase([source])
    first = sync(database, TARGET, apply=True, runtime=RUNTIME)
    assert first['inserted_items'] == 1 and first['inserted_records'] == 4
    database.rows['inbox', source['id']]['course'] = {'id': 'existing-course', 'name': '原课程'}
    database.rows['inbox', source['id']]['processing_status'] = 'needs_review'
    database.rows['inbox_progress', source['id']] = {'archived': True}
    database.rows['event', 'keep'] = {'status': 'active'}
    database.rows['task', 'keep'] = {'status': 'todo'}
    database.rows['review', 'keep'] = {'adopted': True}
    before = deepcopy(database.rows)
    second = sync(database, TARGET, apply=True, runtime=RUNTIME)
    assert second['inserted_records'] == 0 and second['duplicate_prevented'] == 1
    assert database.rows == before
    alias = item('different-id')
    alias['canonical_url'] = source['canonical_url']
    database.items = [alias]
    assert sync(database, TARGET, apply=True, runtime=RUNTIME)['inserted_items'] == 0
    assert database.rows == before


def test_dry_run_is_read_only_and_does_not_borrow_publisher_identity():
    database = FakeDatabase([item()])
    result = sync(database, TARGET)
    assert result['ready'] == 1 and result['inserted_records'] == 0
    assert not database.rows
    assert database.calls[0][0] == 'SET TRANSACTION READ ONLY'
    assert not any('INSERT ' in sql or 'UPDATE ' in sql or 'DELETE ' in sql for sql, _ in database.calls)


def test_historical_approval_cannot_include_a_later_unapproved_source_id():
    database = FakeDatabase([item('approved-original'), item('later-not-approved')])
    result = sync(database, TARGET, apply=True, runtime=RUNTIME, approved_ids=['approved-original'])
    assert result['inserted_items'] == 1
    assert ('raw_item', 'approved-original') in database.rows
    assert ('raw_item', 'later-not-approved') not in database.rows
    before = deepcopy(database.rows)
    assert sync(database, TARGET, apply=True, runtime=RUNTIME, approved_ids=[])['inserted_items'] == 0
    assert database.rows == before


def test_failure_rolls_back_entire_item_and_retry_uses_same_ids():
    database = FakeDatabase([item()])
    database.fail_kind = 'inbox'
    with pytest.raises(RuntimeError):
        sync(database, TARGET, apply=True, runtime=RUNTIME)
    assert not database.rows
    database.fail_kind = None
    assert sync(database, TARGET, apply=True, runtime=RUNTIME)['inserted_records'] == 4
    assert project(item()) == project(item())
    assert database.rows['raw_item', item()['id']]['eric_archive'] == ARCHIVER


@pytest.mark.parametrize('owner,allowed', [('33333333-3333-4333-8333-333333333333', True), (OWNER, False)])
def test_wrong_owner_or_missing_existing_permissions_fails_closed(owner, allowed):
    database = FakeDatabase([item()])
    database.owner, database.allowed = owner, allowed
    with pytest.raises(ValueError):
        sync(database, TARGET, apply=True, runtime=RUNTIME)
    assert not database.rows


def test_existing_tombstone_is_never_resurrected():
    database = FakeDatabase([item()])
    database.rows['raw_item', item()['id']] = {'deleted': True}
    before = deepcopy(database.rows)
    assert sync(database, TARGET, apply=True, runtime=RUNTIME)['inserted_items'] == 0
    assert database.rows == before


def test_unverified_apply_refused_before_connecting():
    database = FakeDatabase([item()])
    with pytest.raises(ValueError):
        sync(database, TARGET, apply=True)
    assert not database.calls


def test_target_binding_resolves_the_exact_approved_pair_without_publishing_account_ids():
    database = FakeDatabase([])
    digest = hashlib.sha256(f'{WORKSPACE}:{OWNER}'.encode()).hexdigest()
    assert resolve_target(database, TARGET.database_id, expected_binding=digest) == TARGET
    assert database.calls[0][0] == 'SET TRANSACTION READ ONLY'


def test_unknown_target_binding_never_falls_back_to_the_first_workspace():
    database = FakeDatabase([])
    with pytest.raises(ValueError):
        resolve_target(database, TARGET.database_id, expected_binding='0' * 64)
    assert not database.rows


def test_unfetched_or_empty_source_is_deferred_without_fabricating_content():
    assert not project(item(fetched_at=None))
    assert not project(item(original_text=''))
    records = project(item(original_text='', body_status='document_only', attachments=[{'url': 'https://www.zqu.edu.cn/a.pdf'}]))
    assert records[1]['payload']['original_text'] == ''
    assert records[1]['payload']['web']['attachments'][0]['url'].endswith('a.pdf')
