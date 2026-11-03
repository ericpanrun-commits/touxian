"""Actual adapter acceptance in a private Attention Desk DB; no real LLM requests."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.ai_settings import AISettings
from server.personal_store import PersonalStore
from server.wechat_collect.config import WeChatConfig, SettingsDocument
from server.wechat_collect.service import WeChatService
from server.wechat_collect.scheduler import utc_now


async def main():
    directory = ROOT / 'artifacts' / 'wechat-db0' / ('integration-' + uuid4().hex[:8])
    personal = PersonalStore(directory / 'personal.sqlite3')
    personal.initialize()
    service = WeChatService(personal, AISettings(directory / 'ai-settings.json'))
    doc = SettingsDocument(wechat=WeChatConfig(enabled=True, allowed_chats=['26车辆2班（公告群）']))
    await service.save_settings(doc,1)
    # Preserve actual images, without an OCR or paid provider dependency for this check.
    service.media_engine = lambda path,work:([],{})
    await service.request_scan(manual=True,request_key=str(uuid4()),reason='db_acceptance')
    await asyncio.gather(*service.scan_tasks)
    first = service.last_result
    first_metrics = dict(service.adapter.last_metrics)
    await service.request_scan(manual=True,request_key=str(uuid4()),reason='db_acceptance_no_change')
    await asyncio.gather(*service.scan_tasks)
    second = service.last_result
    service.analysis.recover_responses = lambda: (_ for _ in ()).throw(AssertionError('no LLM/recovery in no-change'))
    await service.process_queue()
    with personal.connection() as c:
        report = {'first_scan':first,'second_scan':second,'first_metrics':first_metrics,
                  'second_metrics':service.adapter.last_metrics,
                  'persisted_messages':c.execute('SELECT count(*) FROM wechat_message').fetchone()[0],
                  'stable_identities':c.execute('SELECT count(*) FROM wechat_db_message').fetchone()[0],
                  'pending_reviews':c.execute("SELECT count(*) FROM raw_review WHERE status='pending'").fetchone()[0],
                  'llm_calls':c.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0],
                  'formal_events':c.execute('SELECT count(*) FROM event').fetchone()[0],
                  'cursor':dict(c.execute('SELECT * FROM wechat_db_cursor').fetchone()) if c.execute('SELECT count(*) FROM wechat_db_cursor').fetchone()[0] else None,
                  'foreign_key_errors':len(c.execute('PRAGMA foreign_key_check').fetchall()),
                  'messages_sent':0,'unauthorized_message_bodies_read':0}
    report['PASS'] = (first in {'changed','no_change'} and second=='no_change' and report['llm_calls']==0
                      and report['second_metrics']['message_body_queries']==0 and report['foreign_key_errors']==0)
    (ROOT / 'artifacts/wechat-db0/results/integration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    await service.close()
    return 0 if report['PASS'] else 1


if __name__=='__main__':
    raise SystemExit(asyncio.run(main()))
