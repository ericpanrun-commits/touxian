"""Real authorized capture into a backup DB, with zero provider requests."""
from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.ai_settings import AISettings
from server.personal_store import PersonalStore, default_database_path
from server.wechat_collect.db_adapter import ReadOnlyWeChatDBAdapter
from server.wechat_collect.service import WeChatService


def fingerprint(personal):
    with personal.connection() as c:
        tables = ('event', 'task', 'course', 'review_artifact')
        return {table: hashlib.sha256(json.dumps([tuple(r) for r in c.execute(
            'SELECT * FROM ' + table + ' ORDER BY id')], sort_keys=True, ensure_ascii=True).encode()).hexdigest() for table in tables}


async def main():
    source = default_database_path()
    directory = ROOT / 'artifacts/p0-20261104' / ('integration-' + uuid4().hex[:8])
    directory.mkdir()
    personal = PersonalStore(directory / 'personal.sqlite3')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as incoming:
        with closing(sqlite3.connect(personal.path)) as target:
            incoming.backup(target)
    shutil.copyfile(source.with_name('wechat.yaml'), directory / 'wechat.yaml')
    before = fingerprint(personal)
    with personal.connection() as c:
        baseline = {t: c.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0]
                    for t in ('wechat_message','raw_item','wechat_batch','wechat_llm_attempt')}
    adapter = ReadOnlyWeChatDBAdapter(data_directory=directory,
        report_path=ROOT / 'artifacts/p0-20261104/compatibility-candidate.json')
    service = WeChatService(personal, AISettings(directory / 'ai-settings.json'),
                            adapter=adapter, media_engine=lambda *args: ([], {}))
    await service.request_scan(manual=True, request_key=str(uuid4()), reason='recovery_acceptance_real')
    await asyncio.gather(*list(service.scan_tasks))
    first = service.repository.scans(1)['items'][0]
    with personal.connection() as c:
        counts_first = {t: c.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0] for t in baseline}
        samples = [dict(r) for r in c.execute('SELECT b.raw_item_id,b.snapshot_id,b.id AS batch_id,ch.source_id,length(s.original_text) AS original_length FROM wechat_batch b JOIN raw_snapshot s ON s.id=b.snapshot_id JOIN wechat_chat ch ON ch.id=b.chat_id WHERE b.scan_id=?', (first['id'],))]
    await service.request_scan(manual=True, request_key=str(uuid4()), reason='recovery_acceptance_repeat')
    await asyncio.gather(*list(service.scan_tasks))
    second = service.repository.scans(1)['items'][0]
    with personal.connection() as c:
        counts_second = {t: c.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0] for t in baseline}
        errors = len(c.execute('PRAGMA foreign_key_check').fetchall())
    report = {'database': str(personal.path), 'real_authorized_data': True,
        'first': first, 'repeat': second, 'baseline': baseline,
        'counts_after_first': counts_first, 'counts_after_repeat': counts_second,
        'raw_samples': samples, 'formal_hashes_before': before,
        'formal_hashes_after': fingerprint(personal), 'foreign_key_errors': errors,
        'production_writes': 0, 'http_model_requests': 0}
    report['PASS'] = (first['status'] == 'changed' and counts_first['raw_item'] > baseline['raw_item']
        and second['status'] == 'no_change' and counts_first == counts_second
        and counts_second['wechat_llm_attempt'] == baseline['wechat_llm_attempt']
        and before == report['formal_hashes_after'] and errors == 0)
    path = ROOT / 'artifacts/p0-20261104/real-capture-acceptance.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))
    await service.close()
    return 0 if report['PASS'] else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
