"""One reviewed Eric OS batch via its existing API; never release background holds.

Default is a local preflight. Execute only through a separately approved platform
tool call, bound to the exact reviewed SHA256. This is not a queue or scheduler.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechat_backlog_plan import ReadOnlyStore
from server.cloud_store import ProcessLock
from server.cloud_sync.minimized_projection import minimized_projection_preview
from server.cloud_sync.settings import ENDPOINT, WorkerSettings
from server.personal_store import default_database_path, encode
from server.recovery_controls import controls

HASH = '908e472419f0d680b01a0fda841aba970e51342cddac39df80ae6a78a675e60b'
WORKSPACE = '50e5a3ac-dd15-4f0a-a93c-ace4b735d16d'
OWNER = 'fd779e27-adcd-41f5-b9b0-748ea9267956'
PROCESSOR = 'bc5c2a70-d5c1-449c-b8c3-d72b8a02624d'
FOLDER = ROOT / 'artifacts/p0-20261104'
OUTPUT = FOLDER / 'limited-upload'


class LimitedError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise LimitedError(code)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def held(personal):
    state = controls(personal.path)
    require(state == {'hold_cloud_uploads': True, 'pause_paid_ai': True, 'state': 'configured'},
            'background_holds_must_remain_enabled')


def verify_frozen(personal, samples, bundle):
    current = minimized_projection_preview(personal, samples)
    require(current == bundle, 'source_or_reviewed_bundle_changed')
    require(bundle['bundle_hash'] == HASH and hashlib.sha256(encode(bundle['records']).encode()).hexdigest() == HASH,
            'reviewed_hash_mismatch')
    require(bundle['preview_only'] is True and bundle['upload_authorized'] is False,
            'preview_file_is_not_an_authorization')
    require(len(bundle['records']) == 8 and bundle['resources'] == [] and len(bundle['raw_item_ids']) == 2,
            'reviewed_scope_changed')
    return bundle['records']


def validate_remote_preflight(preflight):
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(preflight['at'])).total_seconds()
    require(-60 <= age <= 900, 'remote_preflight_stale')
    require(preflight['workspace'] == {'id': WORKSPACE, 'owner_id': OWNER}, 'workspace_owner_changed')
    processors = preflight['processors']
    require(len(processors) == 1 and processors[0]['id'] == PROCESSOR
            and processors[0]['workspace_id'] == WORKSPACE and processors[0]['revoked_at'] is None,
            'processor_scope_changed')
    last = datetime.fromisoformat(preflight['publisher']['last_seen_at'])
    require((datetime.now(timezone.utc) - last).total_seconds() > 120, 'another_publisher_may_be_active')
    require(preflight['target_records'] == [], 'unexpected_existing_target_records')


def execute(personal, samples, bundle, preflight, config, client, journal_path):
    """No retries. Only hello + exact publish + exact idempotency replay may POST."""
    held(personal)
    records = verify_frozen(personal, samples, bundle)
    validate_remote_preflight(preflight)
    require(config and config['enabled'] and config['endpoint'] == ENDPOINT
            and config['workspace_id'] == WORKSPACE and config['processor_id'] == PROCESSOR,
            'local_processor_identity_changed')
    require(not journal_path.exists(), 'execution_already_attempted_inspect_receipt_first')
    targets = {(r['kind'], r['id']): r['source_hash'] for r in records}
    epoch = str(uuid4())
    journal = {'started_at': stamp(), 'endpoint': ENDPOINT, 'workspace_id': WORKSPACE,
               'processor_id': PROCESSOR, 'bundle_hash': HASH, 'epoch': epoch,
               'raw_count': 2, 'record_count': 8, 'attachment_count': 0,
               'outcome': 'started', 'requests': [], 'receipts': [], 'model_requests': 0}
    save(journal_path, journal)

    def request(method, path, body=None):
        held(personal)
        if method == 'POST':
            if path == '/worker/hello':
                require(body == {'epoch': epoch}, 'unexpected_hello_body')
            else:
                require(path == '/worker/publish'
                        and body == {'epoch': epoch, 'generation': 1, 'records': records},
                        'unreviewed_request_blocked')
            require(sum(r['method'] == 'POST' for r in journal['requests']) < 3, 'write_limit_exceeded')
        else:
            require(method == 'GET' and path.startswith('/worker/records?offset='), 'unreviewed_route_blocked')
        entry = {'method': method, 'path': path, 'started_at': stamp(), 'state': 'requested'}
        journal['requests'].append(entry)
        save(journal_path, journal)
        try:
            response = client.request(method, ENDPOINT + path,
                                      headers={'X-Eric-Worker-Key': config['worker_key']},
                                      **({'json': body} if method == 'POST' else {}))
            entry['status'] = response.status_code
            require(response.status_code == 200, 'api_request_failed_inspect_before_retry')
            value = response.json()
            entry['state'] = 'acknowledged'
            save(journal_path, journal)
            return value
        except Exception:
            entry['state'] = 'unconfirmed'
            journal['outcome'] = 'inspect_before_retry'
            save(journal_path, journal)
            raise LimitedError('request_unconfirmed_no_automatic_retry') from None

    def catalogue():
        found = {}
        offset = 0
        while offset <= 100_000:
            page = request('GET', '/worker/records?offset=' + str(offset))
            require(isinstance(page.get('items'), list) and page.get('limit') == 500, 'catalogue_protocol_changed')
            for row in page['items']:
                key = (row['kind'], row['id'])
                if key in targets:
                    require(key not in found, 'duplicate_remote_target')
                    found[key] = row['source_hash']
            if len(page['items']) < page['limit']:
                return found
            offset += len(page['items'])
        raise LimitedError('catalogue_limit_exceeded')

    require(catalogue() == {}, 'target_appeared_after_preflight')
    hello = request('POST', '/worker/hello', {'epoch': epoch})
    require(hello.get('workspace_id') == WORKSPACE and hello.get('record_catalogue') is True,
            'hello_workspace_mismatch')
    body = {'epoch': epoch, 'generation': 1, 'records': records}
    for purpose in ('initial_publish', 'same_ids_idempotency_check'):
        receipt = request('POST', '/worker/publish', body)
        require(type(receipt.get('revision')) is int and receipt['revision'] >= 0, 'invalid_publish_receipt')
        journal['receipts'].append({'purpose': purpose, 'revision': receipt['revision'], 'at': stamp()})
        save(journal_path, journal)
        require(catalogue() == targets, 'published_catalogue_mismatch_stop')
    held(personal)
    journal.update(outcome='published_and_replayed', completed_at=stamp(),
                   idempotent_highwater_unchanged=journal['receipts'][0]['revision'] == journal['receipts'][1]['revision'],
                   background_uploads_held=True, paid_ai_paused=True)
    save(journal_path, journal)
    return journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--approved-bundle')
    args = parser.parse_args()
    personal = ReadOnlyStore(default_database_path())
    samples = read(FOLDER / 'wechat-minimized-text-samples.json')
    bundle = read(FOLDER / 'wechat-minimized-projection-preview.json')
    held(personal)
    records = verify_frozen(personal, samples, bundle)
    if not args.execute:
        print(json.dumps({'preflight': 'passed', 'execute': False, 'bundle_hash': HASH,
                          'raw_count': 2, 'record_count': len(records), 'attachment_count': 0,
                          'max_post_requests': 3, 'posts': ['hello', 'publish', 'identical_publish_for_idempotency'],
                          'target_tables': ['eric_app_records', 'eric_app_changes', 'eric_app_workers', 'eric_app_processors'],
                          'background_holds_unchanged': True}))
        return
    require(args.approved_bundle == HASH, 'explicit_exact_bundle_approval_required')
    OUTPUT.mkdir(exist_ok=True)
    lock = ProcessLock(OUTPUT)
    lock.acquire()
    try:
        config = WorkerSettings(personal.path.with_name('cloud-worker-settings.json')).read()
        with httpx.Client(timeout=httpx.Timeout(90, connect=10), follow_redirects=False) as client:
            result = execute(personal, samples, bundle, read(OUTPUT / 'remote-preflight.json'),
                             config, client, OUTPUT / 'execution-receipt.json')
        print(json.dumps(result, ensure_ascii=True))
    finally:
        lock.close()


if __name__ == '__main__':
    try:
        main()
    except (LimitedError, ValueError) as error:
        print(json.dumps({'outcome': 'stopped', 'reason': str(error)}, ensure_ascii=True))
        raise SystemExit(1) from None
