"""A missing historical page must not postpone the current notice list for days."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from server.actions.store import PostgresOfficialStore
from server.official_http import PublicHTMLClient
from server.official_service import OfficialSourceMonitor
from server.official_sources import SourceFailure
from server.official_store import OfficialStore
from server.personal_store import PersonalStore
from server.test_official_information import TODAY, document, listing, seed, source


@pytest.fixture
def captured(tmp_path):
    personal = PersonalStore(tmp_path / 'personal.sqlite3')
    personal.initialize()
    src = source()
    store = OfficialStore(personal)
    store.configure([src])
    old = seed(personal, id=1, title='历史通知', body='已保存的历史原文。', s=src)
    prior = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    with personal.connection(write=True) as c:
        c.execute("UPDATE official_source SET failure_count=10,backfill_completed_at=? WHERE source_id=?", (prior, src.id))
        c.execute('UPDATE official_fetch_state SET last_fetched_at=? WHERE raw_item_id=?', (prior, old['raw_item_id']))
        before = c.execute('SELECT original_text FROM raw_snapshot WHERE id=?', (old['raw_snapshot_id'],)).fetchone()[0]
    return personal, src, old, before


def run_scan(tmp_path, captured, listed, statuses):
    personal, src, _, _ = captured
    config = tmp_path / 'sources.json'
    config.write_text(json.dumps({'version': 1, 'sources': [src.model_dump()]}), encoding='utf-8')
    calls = []
    def factory(current, **options):
        def response(request):
            url = str(request.url)
            calls.append(url)
            if url == current.list_url:
                return httpx.Response(200, headers={'content-type': 'text/html'}, content=listing(listed))
            status = statuses.get(url, 200)
            body = document('历史通知', '已保存的历史原文。') if url.endswith('/1.htm') else document('当前通知', '当前原文。')
            return httpx.Response(status, headers={'content-type': 'text/html'}, content=body)
        return PublicHTMLClient(current, transport=httpx.MockTransport(response), resolver=lambda _: None, delay=0, retries=0, **options)
    notifications = type('Notifications', (), {'notify_windows': lambda *a: None})()
    monitor = OfficialSourceMonitor(personal, config_path=config, client_factory=factory,
                                    start_scheduler=False, notifications=notifications)
    result = monitor.run_once([src.id])
    monitor.close()
    with personal.connection() as c:
        state = dict(c.execute('SELECT * FROM official_source WHERE source_id=?', (src.id,)).fetchone())
        latest = dict(c.execute('SELECT * FROM official_run ORDER BY started_at DESC LIMIT 1').fetchone())
    return result, state, latest, calls


@pytest.mark.parametrize('http_status', [404, 410])
def test_saved_unlisted_404_preserves_warning_but_keeps_regular_list_schedule(tmp_path, captured, http_status):
    personal, src, old, original = captured
    missing = 'https://unit.zqu.edu.cn/info/1/1.htm'
    result, state, latest, calls = run_scan(tmp_path, captured, [(2, '当前通知', TODAY)], {missing: http_status})
    assert missing in calls and result['failed'] == 1
    assert latest['error_code'] == 'historical_unavailable' and state['status'] == 'partial'
    assert state['failure_count'] == 11  # The historical issue remains visible.
    assert timedelta(0) < datetime.fromisoformat(state['next_check_at']) - datetime.now(timezone.utc) < timedelta(hours=2)
    with personal.connection() as c:
        assert c.execute('SELECT original_text FROM raw_snapshot WHERE id=?', (old['raw_snapshot_id'],)).fetchone()[0] == original
        assert c.execute('SELECT fetch_error FROM raw_item WHERE id=?', (old['raw_item_id'],)).fetchone()[0].startswith(f'HTTP {http_status}')
        assert c.execute('SELECT count(*) FROM event').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM task').fetchone()[0] == 0


def test_restored_historical_page_clears_the_warning_on_a_later_scan(tmp_path, captured):
    personal, _, old, original = captured
    missing = 'https://unit.zqu.edu.cn/info/1/1.htm'
    run_scan(tmp_path, captured, [(2, '当前通知', TODAY)], {missing: 404})
    _, state, latest, calls = run_scan(tmp_path, captured, [(2, '当前通知', TODAY)], {missing: 200})
    assert missing in calls and latest['error_code'] == '' and state['status'] == 'ok'
    assert state['failure_count'] == 0
    with personal.connection() as c:
        assert c.execute('SELECT original_text FROM raw_snapshot WHERE id=?', (old['raw_snapshot_id'],)).fetchone()[0] == original
        assert c.execute('SELECT fetch_error FROM raw_item WHERE id=?', (old['raw_item_id'],)).fetchone()[0] == ''


@pytest.mark.parametrize(('listed', 'statuses'), [
    ([(1, '历史通知', TODAY)], {'https://unit.zqu.edu.cn/info/1/1.htm': 404}),
    ([(2, '当前通知', TODAY)], {'https://unit.zqu.edu.cn/info/1/1.htm': 500}),
    ([(2, '当前通知', TODAY)], {'https://unit.zqu.edu.cn/info/1/1.htm': 404, 'https://unit.zqu.edu.cn/info/1/2.htm': 404}),
])
def test_current_or_transient_failures_keep_existing_backoff(tmp_path, captured, listed, statuses):
    _, state, latest, _ = run_scan(tmp_path, captured, listed, statuses)
    assert latest['error_code'] != 'historical_unavailable'
    assert datetime.fromisoformat(state['next_check_at']) - datetime.now(timezone.utc) > timedelta(days=1)


@pytest.mark.parametrize(('status', 'code'), [(404, 'http_error'), (410, 'http_error'), (500, 'temporary_http')])
def test_http_status_is_available_without_changing_existing_error_codes(status, code):
    src = source()
    transport = httpx.MockTransport(lambda _: httpx.Response(status))
    with PublicHTMLClient(src, transport=transport, resolver=lambda _: None, delay=0, retries=0) as client:
        with pytest.raises(SourceFailure) as failure:
            client.get(src.list_url)
    assert failure.value.code == code and failure.value.http_status == status


def test_cloud_store_keeps_partial_status_but_does_not_back_off_historical_only():
    class Cursor:
        def fetchone(self):
            return {'state': {'failure_count': 10}, 'config': {'polling_interval': 1800}}
    class Database:
        def __init__(self): self.next_checks, self.states = [], []
        @contextmanager
        def connection(self): yield self
        def execute(self, sql, params=()):
            if sql.startswith('SELECT'): return Cursor()
            if sql.startswith('UPDATE zqu_worker.sources'):
                self.states.append(params[0].obj)
                self.next_checks.append(params[1])
            return Cursor()
    db = Database()
    counts = {'new': 0, 'changed': 0, 'unchanged': 1, 'saved': 1, 'requests': 2,
              'failed': 1, 'historical_unavailable': 1}
    PostgresOfficialStore(db).finish('fixture', counts, SourceFailure('historical_unavailable', 'old 404', status='partial'))
    assert db.states[-1]['status'] == 'partial' and db.states[-1]['error'] == 'historical_unavailable'
    assert db.states[-1]['failure_count'] == 11
    assert timedelta(minutes=59) < db.next_checks[-1] - datetime.now(timezone.utc) < timedelta(hours=2)
    PostgresOfficialStore(db).finish('fixture', counts, SourceFailure('http_error', 'current 404'))
    assert db.next_checks[-1] - datetime.now(timezone.utc) > timedelta(days=1)
