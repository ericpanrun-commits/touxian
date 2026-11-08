"""Prepare or activate a separately authorized, exact set of ongoing text sources.

This tool cannot infer consent. --activate is for an operator with explicit
content-transfer approval, separate from collection and API payment approval.
It never resets historical jobs, starts Collector or enables cloud uploads.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.wechat_collect.config import ConfigStore
from server.wechat_collect.deepseek_content_gate import FILENAME,POLICY,binding_digest,read_grant
from server.wechat_collect.deepseek_worker import CONTROLS,checked_controls,launch_if_authorized


def exclusive_json(path,value):
    with path.open('x',encoding='utf8') as output:
        json.dump(value,output,ensure_ascii=False,indent=2)
        output.flush()
        os.fsync(output.fileno())


def prepare(directory,chat_ids,authorization_id):
    checked_controls(directory/'recovery-controls.json',paid_paused=True)
    expected=set(chat_ids)
    if not expected or len(expected)!=len(chat_ids):
        raise ValueError('explicit_unique_chat_ids_required')
    config=ConfigStore(directory/'wechat.yaml').read().wechat
    allowed={chat.id for chat in config.priority_chats if chat.enabled and config.allows(chat)}
    if not config.enabled or not expected<=allowed:
        raise ValueError('requested_chat_not_currently_authorized_for_collection')
    with sqlite3.connect((directory/'personal.sqlite3').as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        rows=connection.execute('SELECT ch.id,ch.source_id,ch.binding_json,COALESCE(MAX(m.ingest_seq),0) watermark FROM wechat_chat ch LEFT JOIN wechat_message m ON m.chat_id=ch.id WHERE ch.enabled=1 GROUP BY ch.id').fetchall()
        selected=[row for row in rows if row['id'] in expected]
        if {row['id'] for row in selected}!=expected:
            raise ValueError('source_scope_changed')
        for row in selected:
            binding=json.loads(row['binding_json'] or '{}')
            if not binding.get('account_scope') or not binding.get('internal_chat_id'):
                raise ValueError('verified_source_binding_required')
    value={'version':1,'enabled':True,'authorization_id':authorization_id,
        'destination':'https://api.deepseek.com','policy':POLICY,'mode':'continuous',
        'not_before':datetime.now(timezone.utc).isoformat(),
        'chat_watermarks':{row['id']:row['watermark'] for row in selected},
        'source_bindings':{row['id']:binding_digest(row['source_id'],row['binding_json']) for row in selected}}
    read_grant(json.dumps(value).encode())
    return value


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-directory',type=Path,required=True)
    parser.add_argument('--chat-id',action='append',required=True)
    parser.add_argument('--authorization-id',required=True)
    parser.add_argument('--activate',action='store_true')
    parser.add_argument('--start',action='store_true')
    parser.add_argument('--unlimited-test-tokens',action='store_true')
    args=parser.parse_args()
    if args.start and not args.activate:
        parser.error('--start requires --activate')
    directory=args.data_directory.resolve()
    value=prepare(directory,args.chat_id,args.authorization_id)
    if args.activate:
        # Preflight both paths; existing evidence is never overwritten or reused.
        if any((directory/name).exists() for name in (FILENAME,CONTROLS)):
            raise ValueError('activation_files_already_exist_review_required')
        exclusive_json(directory/FILENAME,value)
        exclusive_json(directory/CONTROLS,{'version':1,'hold_cloud_uploads':True,'pause_paid_ai':False,
            'unlimited_test_tokens':args.unlimited_test_tokens})
    child=launch_if_authorized(directory) if args.start else None
    print(json.dumps({'activated':args.activate,'chat_count':len(value['chat_watermarks']),
        'not_before':value['not_before'],'worker_pid':child.pid if child else None,
        'global_holds_unchanged':True,'historical_jobs_reset':0,'cloud_enabled':False,
        'unlimited_test_tokens':args.unlimited_test_tokens}))


if __name__=='__main__':
    main()
