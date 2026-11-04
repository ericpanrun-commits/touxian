"""Read-only evidence from the real local service, database and pre-start backup."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.personal_store import default_database_path


def main():
    directory=ROOT/'artifacts/p0-20261104'
    checkpoint=json.loads((directory/'checkpoint.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765',timeout=20,trust_env=False) as client:
        health=client.get('/api/health?check_integrations=false');health.raise_for_status()
        scans=client.get('/api/personal/wechat/scans?limit=5');scans.raise_for_status()
        status=client.get('/api/personal/wechat/status');status.raise_for_status()
    with closing(sqlite3.connect(default_database_path().as_uri()+'?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
        current={};hashes={}
        for table in checkpoint['counts']:
            rows=[dict(r) for r in c.execute('SELECT * FROM '+table+' ORDER BY id')]
            current[table]=len(rows)
            hashes[table]=hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
        groups=[dict(r) for r in c.execute('SELECT * FROM wechat_chat ORDER BY id')]
        recent=[dict(r) for r in c.execute('SELECT * FROM wechat_chat_scan ORDER BY created_at DESC LIMIT 20')]
        schema={r['name']:r['sql'] for r in c.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND (name IN ('attachment','wechat_message','wechat_batch','wechat_chat','raw_item','raw_snapshot') OR name LIKE 'wechat_media%')")}
        new_raw=[dict(r) for r in c.execute('SELECT b.raw_item_id,b.snapshot_id,b.id AS batch_id,ch.source_id,ch.name,length(s.original_text) AS original_length FROM wechat_batch b JOIN raw_snapshot s ON s.id=b.snapshot_id JOIN wechat_chat ch ON ch.id=b.chat_id WHERE b.created_at>? ORDER BY b.created_at',(checkpoint['at'],))]
        fk=len(c.execute('PRAGMA foreign_key_check').fetchall())
        existing_changes={}
        with closing(sqlite3.connect(Path(checkpoint['directory'])/'personal.sqlite3')) as old:
            old.row_factory=sqlite3.Row;old.execute('PRAGMA query_only=ON')
            for table in ('raw_item','raw_snapshot','wechat_message','wechat_llm_attempt'):
                changes={}
                missing=0;modified=0
                for before in old.execute('SELECT * FROM '+table):
                    after=c.execute('SELECT * FROM '+table+' WHERE id=?',(before['id'],)).fetchone()
                    if after is None:missing+=1;continue
                    fields=[key for key in before.keys() if before[key]!=after[key]]
                    modified+=bool(fields)
                    for key in fields:changes[key]=changes.get(key,0)+1
                existing_changes[table]={'deleted':missing,'modified':modified,'changed_fields':changes}
    result={'at':datetime.now(timezone.utc).isoformat(),'checkpoint':checkpoint['directory'],
        'counts_before':checkpoint['counts'],'counts_after':current,'sha256_after':hashes,
        'formal_domains_unchanged':all(hashes[t]==checkpoint['sha256'][t] for t in ('event','task','course','review_artifact')),
        'llm_attempts_unchanged':hashes['wechat_llm_attempt']==checkpoint['sha256']['wechat_llm_attempt'],
        'foreign_key_errors':fk,'existing_row_changes':existing_changes,
        'wechat_configuration_unchanged':(Path(checkpoint['directory'])/'wechat-before.yaml').read_bytes()==default_database_path().with_name('wechat.yaml').read_bytes(),
        'health':health.json(),'scans':scans.json(),'status':status.json(),
        'groups':groups,'recent_group_scans':recent,'new_raw_samples':new_raw,'schema':schema}
    (directory/'local-production-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:result[key] for key in ('at','counts_before','counts_after','formal_domains_unchanged','llm_attempts_unchanged','foreign_key_errors')},ensure_ascii=True))
    print(json.dumps({'scans':result['scans']['items'],'sources':result['health'].get('collection_health',{}).get('sources',[])},ensure_ascii=True))


if __name__=='__main__':main()
