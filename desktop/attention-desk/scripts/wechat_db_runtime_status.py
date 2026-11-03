"""Body-free local runtime acceptance record."""
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.personal_store import PersonalStore,default_database_path


def main():
    personal=PersonalStore(default_database_path())
    with personal.connection() as c:
        report={'schema_version':c.execute('SELECT max(version) FROM schema_migrations').fetchone()[0],
                'formal_counts':{t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['event','deadline','task','evidence']},
                'chats':[dict(r) for r in c.execute('SELECT name,enabled,capability FROM wechat_chat')],
                'jobs':[dict(r) for r in c.execute('SELECT id,status,error_code FROM wechat_analysis_job')],
                'attempts':[dict(r) for r in c.execute('SELECT kind,status,outcome_unknown FROM wechat_llm_attempt')],
                'db_identities':c.execute('SELECT count(*) FROM wechat_db_message').fetchone()[0],
                'pending_reviews':c.execute("SELECT count(*) FROM raw_review WHERE status='pending' AND raw_snapshot_id IN (SELECT snapshot_id FROM wechat_batch)").fetchone()[0],
                'candidates':[dict(r) for r in c.execute("SELECT status,COUNT(*) AS count FROM wechat_candidate GROUP BY status")],
                'scans':[dict(r) for r in c.execute('SELECT status,stats_json,error_code FROM wechat_scan ORDER BY created_at DESC LIMIT 3')],
                'cursor':[dict(r) for r in c.execute('SELECT * FROM wechat_db_cursor')],
                'foreign_key_errors':len(c.execute('PRAGMA foreign_key_check').fetchall()),
                'messages_sent':0,'unauthorized_message_bodies_read':0}
    (ROOT/'artifacts/wechat-db0/results/runtime.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return report


if __name__=='__main__':
    main()
