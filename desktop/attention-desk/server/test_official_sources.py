"""Real public ZQU DOM cases; mock transports test contracts without live requests."""
import asyncio
import hashlib
import json
import socket
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from server.official_sources import SourceConfig, SourceFailure, canonical_url, load_sources
from server.official_http import HTMLResult, PublicHTMLClient, public_dns
from server.official_parsers import parse_article, parse_list
from server.official_service import OfficialSourceMonitor
from server.official_store import OfficialStore
from server.personal_store import PersonalStore
from server.review_store import ReviewStore
from server.extraction_service import ExtractionService
from server.extraction_validation import validate_candidate
from server.source_metadata import model_source_context
from server.test_extraction_review import candidate, confirmation, FixtureProvider, TestSettings, counts, finish

ROOT = Path(__file__).with_name('fixtures') / 'official'
PAGES = {p['file'].removesuffix('.html'): p for p in json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))['pages']}
SOURCES = {s.id: s for s in load_sources()}


@pytest.fixture
def store(tmp_path):
    personal = PersonalStore(tmp_path / 'personal.sqlite3')
    personal.initialize()
    OfficialStore(personal).configure(list(SOURCES.values()))
    return personal


def page(name):
    return (ROOT / (name + '.html')).read_bytes()


def article(name):
    p = PAGES[name]
    return parse_article(page(name), p['url'], SOURCES[p['source_id']], {'title': '', 'published_at': None})


def save(store, name, value=None):
    p = PAGES[name]
    return OfficialStore(store).collect(SOURCES[p['source_id']], p['url'], p['url'], value or article(name), HTMLResult(p['url'], page(name), 200, 'real-fixture-etag'))


def reviewed(store, name, dates, actions):
    saved = save(store, name)
    raw = ReviewStore(store).get(saved['raw_snapshot_id'])
    output = candidate(raw['snapshot']['original_text'], dates, actions)
    return raw, output


def test_case_1_one_exact_deadline_full_saved_body_before_ai_and_confirmation(store):
    raw, output = reviewed(store, 'iec_article', [('2026 年 3 月 30 日前', '2026-03-30')], ['填写《中国国际大学生创新大赛（2026）肇庆学院初赛重点推荐项目汇总表》'])
    assert len(raw['snapshot']['original_text']) == 4415
    assert counts(store)['event'] == counts(store)['ai_extraction'] == 0
    provider = FixtureProvider(output)
    service = ExtractionService(store, TestSettings(), provider)
    async def run():
        await service.start(raw['snapshot']['id']); await finish(service)
        again = await service.start(raw['snapshot']['id'])
        assert again['extraction_id'] == ReviewStore(store).get(raw['snapshot']['id'])['extraction_id']
    asyncio.run(run())
    raw = ReviewStore(store).get(raw['snapshot']['id'])
    assert raw['extraction']['status'] == 'valid' and provider.calls == 1
    assert counts(store)['event'] == 0
    event = ReviewStore(store).confirm(raw['snapshot']['id'], confirmation(raw, output))['events'][0]
    assert event['deadlines'][0]['value'] == '2026-03-30'
    assert event['snapshot']['web']['canonical_url'] == PAGES['iec_article']['url']
    for evidence in event['evidence']:
        assert evidence['raw_snapshot_id'] == raw['snapshot']['id']
        assert raw['snapshot']['original_text'][evidence['start_offset']:evidence['end_offset']] == evidence['quote']


def test_case_2_real_multiple_stages_and_associated_actions(store):
    raw, output = reviewed(store, 'jxqc_article', [('2026年10月10日前', '2026-10-10'), ('2026年10月16日前', '2026-10-16'), ('2026年10月23日前', '2026-10-23'), ('2026年10月30日前', '2026-10-30')], ['完成题目申报工作', '对所有拟题进行匿名评审', '学生在指导教师的指导下完成选题', '指导教师上传《任务书》到论文系统'])
    event = ReviewStore(store).confirm(raw['snapshot']['id'], confirmation(raw, output))['events'][0]
    assert len(event['deadlines']) == len(event['tasks']) == 4
    assert len({t['deadline_id'] for t in event['tasks']}) == 4


def test_case_3_publication_is_not_deadline_or_first_date_in_title(store):
    raw, output = reviewed(store, 'iec_article', [('2026 年 3 月 30 日前', '2026-03-30')], ['打包发至邮箱'])
    assert raw['snapshot']['source_time'] == raw['snapshot']['web']['published_at'] == '2026-01-26'
    context = model_source_context(raw['snapshot'])
    assert context['published_at'] != output['events'][0]['deadlines'][0]['normalized_date']
    assert 'body_html' not in json.dumps(context)
    body = b'<table><tr><td><a href="/info/1004/4495.htm">2026-03-30 deadline</a></td><td><span>2026-01-26</span></td></tr></table>'
    entries, _ = parse_list(body, SOURCES['zqu-iec'].list_url, SOURCES['zqu-iec'])
    assert entries[0]['published_at'] == '2026-01-26'


def test_case_4_real_attachments_are_only_metadata_and_warn(store):
    raw, output = reviewed(store, 'iec_article', [('2026 年 3 月 30 日前', '2026-03-30')], ['打包发至邮箱'])
    attachments = raw['snapshot']['web']['attachments']
    assert len(attachments) == 3
    assert {a['extension'] for a in attachments} == {'pdf', 'xls', 'docx'}
    assert all(a['status'] == 'unparsed' and a['filename'] and a['url'].startswith('https://iec.zqu.edu.cn/') for a in attachments)
    assert 'body_html' not in raw['snapshot']['web']
    assert model_source_context(raw['snapshot'])['attachments'] == attachments
    assert len(article('jxqc_article')['attachments']) == 11
    validation = validate_candidate(output, raw['snapshot'])
    output['events'][0]['materials'] = [{'text': '附件 2', 'evidence_ids': []}]
    validation = validate_candidate(output, raw['snapshot'])
    assert any('未下载或解析' in warning['message'] for warning in validation['warnings'])


def test_case_5_real_announcement_has_no_deadline_even_with_publication_and_signature(store):
    raw = ReviewStore(store).get(save(store, 'iec_no_deadline')['raw_snapshot_id'])
    output = candidate(raw['snapshot']['original_text'], [('2026年6月5日', '2026-06-05')], ['确保项目按时保质完成'])
    output['events'][0]['deadlines'] = []
    output['events'][0]['actions'] = []
    event = ReviewStore(store).confirm(raw['snapshot']['id'], confirmation(raw, output))['events'][0]
    assert event['snapshot']['web']['published_at'] == '2026-06-05'
    assert event['deadlines'] == event['tasks'] == []


def test_case_6_real_conditional_postponement_retains_condition_and_original(store):
    raw, output = reviewed(store, 'jwc_article', [('9月11日', '2026-09-11'), ('9月30日', '2026-09-30')], ['网上申请'])
    assert '因第二周补（缓）考课程不及格的重修，可延期到9月30日' in raw['snapshot']['original_text']
    output['events'][0]['notice_kind'] = 'update'
    output['events'][0]['deadlines'][0]['relation'] = 'previous'
    output['events'][0]['uncertainties'] = ['延期仅适用于第二周补（缓）考课程不及格的重修，需人工确认资格。']
    event = ReviewStore(store).confirm(raw['snapshot']['id'], confirmation(raw, output))['events'][0]
    assert len(event['deadlines']) == 1 and event['deadlines'][0]['value'] == '2026-09-30'
    assert '9月11日' in event['snapshot']['original_text']


def test_case_7_simulated_body_change_from_real_notice_keeps_old_event_evidence_and_versions(store):
    raw, output = reviewed(store, 'jxqc_article', [('2026年10月23日前', '2026-10-23')], ['学生在指导教师的指导下完成选题'])
    event = ReviewStore(store).confirm(raw['snapshot']['id'], confirmation(raw, output))['events'][0]
    changed = article('jxqc_article')
    changed['original_text'] = changed['original_text'].replace('2026年10月23日前', '2026年10月24日前')
    new = save(store, 'jxqc_article', changed)
    assert new['kind'] == 'changed' and new['raw_snapshot_id'] != raw['snapshot']['id']
    latest = ReviewStore(store).get(new['raw_snapshot_id'])
    assert latest['snapshot']['web']['version'] == 2 and latest['extraction'] is None
    assert counts(store)['raw_item'] == counts(store)['event'] == 1
    updated_detail = store.get_event(event['id'])
    assert updated_detail['source_update']['id'] == new['raw_snapshot_id']
    assert {key:value for key,value in updated_detail.items() if key!='source_update'} == {key:value for key,value in event.items() if key!='source_update'}
    assert save(store, 'jxqc_article', changed)['kind'] == 'unchanged'
    restarted = PersonalStore(store.path); restarted.initialize()
    assert restarted.get_event(event['id']) == updated_detail
    with restarted.connection(write=True) as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE raw_snapshot_web SET published_at='2099-01-01' WHERE raw_snapshot_id=?", (raw['snapshot']['id'],))


def test_case_8_actual_notice_in_college_home_and_teaching_column_deduplicates(store):
    url = PAGES['jxqc_article']['url']
    for name in ['jxqc_home', 'jxqc_list']:
        entries, _ = parse_list(page(name), PAGES[name]['url'], SOURCES['zqu-jxqc'])
        assert url in {e['url'] for e in entries}
    home = SOURCES['zqu-jxqc'].model_copy(update={'id': 'college-home', 'name': '学院首页测试栏目', 'list_url': 'https://jxqc.zqu.edu.cn/'})
    OfficialStore(store).configure([*list(SOURCES.values())[:3], home])
    first = save(store, 'jxqc_article')
    second = OfficialStore(store).collect(home, url + '#section', url, article('jxqc_article'), HTMLResult(url, page('jxqc_article'), 200))
    assert second['kind'] == 'unchanged' and first['raw_snapshot_id'] == second['raw_snapshot_id']
    assert counts(store)['raw_item'] == counts(store)['raw_snapshot'] == 1
    assert OfficialStore(store).pending_count() == 1
    assert ReviewStore(store).list(source_id=home.id)['total'] == 1
    assert ReviewStore(store).list(source_id='zqu-jxqc')['total'] == 1


@pytest.mark.parametrize('name', list(PAGES))
def test_public_fixture_hashes_and_validated_static_dom(name):
    assert hashlib.sha256(page(name)).hexdigest() == PAGES[name]['fixture_sha256']


@pytest.mark.parametrize('name', ['school', 'jwc_course', 'jxqc_list', 'iec_list', 'xsc_list'])
def test_real_lists_dates_pagination_and_configured_static_modes(name):
    s = SOURCES[PAGES[name]['source_id']]
    entries, next_page = parse_list(page(name), PAGES[name]['url'], s)
    assert len(entries) >= 9 and all(e['published_at'] for e in entries)
    assert len({e['url'] for e in entries}) == len(entries)
    assert next_page or name == 'jxqc_list'


@pytest.mark.parametrize('bad', ['file:///C:/secret.txt', 'javascript:alert(1)', 'https://evil.test/info/1/2.htm', 'https://iec.zqu.edu.cn.evil.test/', 'https://user:secret@iec.zqu.edu.cn/', 'https://127.0.0.1/', 'https://iec.zqu.edu.cn:8080/', 'https://iec.zqu.edu.cn\\@evil.test/', 'https://iec.zqu.edu.cn/\nheader'])
def test_unsafe_urls_rejected(bad):
    with pytest.raises(SourceFailure): canonical_url(bad, ['iec.zqu.edu.cn'])


def test_canonical_normalizes_fragment_tracking_and_query():
    assert canonical_url('https://IEC.ZQU.EDU.CN:443/info/1/2.htm?z=2&utm_source=x&a=1#top', ['iec.zqu.edu.cn']) == 'https://iec.zqu.edu.cn/info/1/2.htm?a=1&z=2'


@pytest.mark.parametrize('address', ['127.0.0.1', '10.2.3.4', '169.254.169.254', '::1'])
def test_private_dns_is_rejected(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', (address, 0))])
    with pytest.raises(SourceFailure) as failure: public_dns('iec.zqu.edu.cn')
    assert failure.value.code == 'private_address'


@pytest.mark.parametrize('mode,code', [('redirect', 'unsafe_url'), ('loop', 'redirect_limit'), ('size', 'response_too_large'), ('stream', 'response_too_large'), ('pdf', 'not_html'), ('timeout', 'timeout'), ('denied', 'access_denied')])
def test_http_redirect_mime_size_timeout_limits(mode, code):
    requests = []
    def handler(request):
        requests.append(request)
        if mode == 'redirect': return httpx.Response(302, headers={'location': 'https://evil.test/'})
        if mode == 'loop': return httpx.Response(302, headers={'location': '/loop'})
        if mode == 'timeout': raise httpx.ReadTimeout('test')
        if mode == 'denied': return httpx.Response(403)
        return httpx.Response(200, headers={'content-type': 'application/pdf' if mode == 'pdf' else 'text/html', **({'content-length': '2000001'} if mode == 'size' else {})}, content=b'x' * (2_000_001 if mode == 'stream' else 20))
    with PublicHTMLClient(SOURCES['zqu-iec'], transport=httpx.MockTransport(handler), resolver=lambda host: None, delay=0) as client:
        with pytest.raises(SourceFailure) as failure: client.get(SOURCES['zqu-iec'].list_url)
    assert failure.value.code == code
    assert len(requests) == (4 if mode == 'loop' else 1)


def test_conditional_headers_and_304_preserve_snapshot_and_clear_error(store):
    saved = save(store, 'iec_article'); o = OfficialStore(store)
    state = o.fetch_state(PAGES['iec_article']['url'])
    o.fetch_error(state['raw_item_id'], SourceFailure('timeout', '旧失败'))
    def handler(req):
        assert req.headers['if-none-match'] == 'real-fixture-etag'
        return httpx.Response(304)
    with PublicHTMLClient(SOURCES['zqu-iec'], transport=httpx.MockTransport(handler), resolver=lambda h: None, delay=0) as c:
        assert c.get(PAGES['iec_article']['url'], etag=state['etag']).status == 304
    assert o.unchanged('zqu-iec', PAGES['iec_article']['url'], state)['raw_snapshot_id'] == saved['raw_snapshot_id']
    assert counts(store)['raw_snapshot'] == 1
    with store.connection() as c: assert c.execute('SELECT fetch_error FROM raw_item').fetchone()[0] == ''


def test_source_failure_isolation_raw_first_dedup_and_restart_enable(tmp_path, store):
    sources = [SOURCES[key].model_copy(update={'max_items': 1}) for key in ['zqu-iec', 'zqu-school', 'zqu-jxqc']]
    config = tmp_path / 'sources.json'
    config.write_text(json.dumps({'version': 1, 'school': '肇庆学院', 'sources': [s.model_dump() for s in sources]}, ensure_ascii=False), encoding='utf-8')
    calls = []
    def factory(source, **kwargs):
        def handler(req):
            calls.append(str(req.url))
            if source.id == 'zqu-iec': raise httpx.ConnectError('isolated source failure')
            mapping = {'zqu-school': ('school', 'school_article'), 'zqu-jxqc': ('jxqc_list', 'jxqc_article')}
            list_name, article_name = mapping[source.id]
            return httpx.Response(200, headers={'content-type': 'text/html'}, content=page(list_name if str(req.url) == source.list_url else article_name))
        return PublicHTMLClient(source, transport=httpx.MockTransport(handler), resolver=lambda host: None, delay=0, **kwargs)
    monitor = OfficialSourceMonitor(store, config_path=config, client_factory=factory, start_scheduler=False)
    monitor.start_scan(); monitor.worker.join(10)
    result = monitor.overview()
    assert result['scan']['done'] == 3 and result['scan']['failed'] == 1
    assert {s['id']: s['status'] for s in result['sources']} == {'zqu-iec': 'unreachable', 'zqu-school': 'ok', 'zqu-jxqc': 'ok'}
    assert counts(store)['raw_snapshot'] == 2 and counts(store)['event'] == counts(store)['ai_extraction'] == 0
    before = counts(store); monitor.start_scan(); monitor.worker.join(10)
    assert counts(store) == before
    monitor.set_enabled('zqu-school', False); monitor.close()
    restarted = OfficialSourceMonitor(store, config_path=config, start_scheduler=False)
    assert not next(s for s in restarted.overview()['sources'] if s['id'] == 'zqu-school')['enabled']
    with pytest.raises(ValueError): restarted.start_scan(['zqu-school'])
    restarted.close()


def test_discovered_identity_retained_when_body_fails_and_redirect_alias_coalesces(store):
    o = OfficialStore(store); p = PAGES['iec_article']; s = SOURCES[p['source_id']]
    identity = o.discover(s, {'url': p['url'], 'title': article('iec_article')['title'], 'published_at': '2026-01-26'})
    o.fetch_error(identity['raw_item_id'], SourceFailure('empty_body', '正文尚未取得'))
    assert counts(store)['raw_item'] == 1 and counts(store)['raw_snapshot'] == 0
    first = save(store, 'iec_article')
    alias = 'https://iec.zqu.edu.cn/info/1004/999999.htm'
    o.discover(s, {'url': alias, 'title': article('iec_article')['title'], 'published_at': '2026-01-26'})
    second = o.collect(s, alias, p['url'], article('iec_article'), HTMLResult(p['url'], page('iec_article'), 200))
    assert second['raw_snapshot_id'] == first['raw_snapshot_id']
    assert counts(store)['raw_item'] == counts(store)['raw_snapshot'] == 1


def test_missing_config_does_not_break_manual_or_existing_events(store, tmp_path):
    monitor = OfficialSourceMonitor(store, config_path=tmp_path / 'absent.json', start_scheduler=False)
    assert monitor.overview()['configuration']['status'] == 'unconfigured'
    from server.extraction_models import RawInput
    raw = ReviewStore(store).collect(RawInput(original_text='手动保存仍可使用。'))
    assert raw['snapshot']['original_text'] == '手动保存仍可使用。'
    monitor.close()


def test_scheduler_checks_only_due_enabled_sources_and_setting_types(store):
    monitor = OfficialSourceMonitor(store, start_scheduler=False)
    monitor.set_enabled('zqu-xsc', False)
    with store.connection(write=True) as c:
        c.execute("UPDATE official_source SET next_check_at='2099-01-01' WHERE source_id='zqu-jwc'")
    due = []
    calls = iter([False, True])
    monitor.stop_event.wait = lambda seconds: next(calls)
    monitor.start_scan = lambda ids: due.extend(ids)
    monitor._schedule()
    assert set(due) == {'zqu-school', 'zqu-jxqc', 'zqu-iec'}
    with pytest.raises(ValueError): monitor.set_enabled([], True)
    with pytest.raises(ValueError): monitor.set_enabled('zqu-school', 'true')
    assert counts(store)['event'] == counts(store)['ai_extraction'] == 0


def test_ignoring_and_resuming_real_official_snapshot_never_calls_ai_or_creates_events(store):
    review = ReviewStore(store)
    raw = review.get(save(store, 'iec_no_deadline')['raw_snapshot_id'])
    ignored = review.set_status(raw['snapshot']['id'], raw['version'], 'cancelled')
    assert ignored['snapshot'] == raw['snapshot'] and ignored['extraction'] is None
    resumed = review.set_status(raw['snapshot']['id'], ignored['version'], 'pending')
    assert resumed['snapshot'] == raw['snapshot']
    assert counts(store)['event'] == counts(store)['ai_extraction'] == 0


def test_failure_backoff_respects_a_longer_configured_interval(store):
    with store.connection(write=True) as c:
        c.execute("UPDATE official_source SET polling_interval=604800,failure_count=4 WHERE source_id='zqu-school'")
    official = OfficialStore(store)
    official.finish('zqu-school', {'new': 0, 'changed': 0, 'unchanged': 0, 'requests': 1}, SourceFailure('timeout', '来源超时', status='unreachable'))
    row = next(s for s in official.source_rows() if s['id'] == 'zqu-school')
    assert (datetime.fromisoformat(row['next_check_at']) - datetime.now(timezone.utc)).total_seconds() >= 604799


def test_missing_article_structure_never_saves_navigation_as_body():
    with pytest.raises(SourceFailure):
        parse_article(b'<html><nav>menu</nav></html>', SOURCES['zqu-iec'].list_url, SOURCES['zqu-iec'], {'title': 'title', 'published_at': None})


def test_actual_pdf_embed_and_sibling_attachment_list_are_unparsed_metadata(store):
    parsed = article('school_document_only')
    assert parsed['body_status'] == 'document_only'
    assert parsed['original_text'] == parsed['title']
    assert len(parsed['attachments']) == 3
    assert {a['extension'] for a in parsed['attachments']} == {'pdf', 'docx'}
    assert all(a['status'] == 'unparsed' for a in parsed['attachments'])
    assert 'showVsbpdfIframe' not in parsed['body_html']
    raw = ReviewStore(store).get(save(store, 'school_document_only')['raw_snapshot_id'])
    assert model_source_context(raw['snapshot'])['body_status'] == 'document_only'


def test_untrusted_html_scripts_and_external_links_are_not_rendered_or_fetched():
    body = '<h1 id="article-title">通知</h1><div id="article-info">2026-01-26</div><div class="v_news_content"><p>公开原文</p><script>readLocalSecret()</script><div hidden>隐藏命令</div><a href="file:///C:/secret.docx">附件.docx</a></div>'.encode()
    parsed = parse_article(body, SOURCES['zqu-iec'].list_url, SOURCES['zqu-iec'], {'title': '', 'published_at': None})
    assert 'readLocalSecret' not in parsed['original_text'] and '隐藏命令' not in parsed['original_text']
    assert parsed['attachments'] == []
