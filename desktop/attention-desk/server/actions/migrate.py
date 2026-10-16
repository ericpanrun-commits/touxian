"""Verified official-only SQLite export and atomic, one-time PostgreSQL import."""
import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from psycopg.types.json import Jsonb

from ..cloud_store import safe_backup
from ..notifications.policy import NotificationPolicyConfig
from .database import PostgresDatabase


def export_snapshot(database, destination):
    destination=Path(destination).resolve()
    backup=safe_backup(database,destination/'sqlite-backups')
    with closing(sqlite3.connect(f'{backup.as_uri()}?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        prefs=dict(db.execute("SELECT * FROM notification_preferences WHERE channel='bark'").fetchone())
        activation=prefs['activation_at']
        if not activation:
            from ..personal_store import now
            activation=now()
        identity=db.execute("SELECT lower(hex(randomblob(16)))").fetchone()[0]
        sources=[]
        for row in db.execute('SELECT * FROM official_source ORDER BY source_id'):
            state=dict(row);config=json.loads(state.pop('config_json'));state.pop('config_hash',None)
            state['enabled']=bool(state['enabled'])
            sources.append({'id':row['source_id'],'config':config,'state':state,'next_check_at':state.get('next_check_at')})
        items=[]
        query='''SELECT r.id,r.title,r.canonical_url,r.original_url,r.published_at,r.discovered_at,r.official_backfill,i.*,
            f.etag,f.last_modified,f.last_fetched_at,s.original_text,w.version_hash,w.attachments_json,w.images_json
            FROM raw_item r LEFT JOIN official_item i ON i.raw_item_id=r.id
            LEFT JOIN official_fetch_state f ON f.raw_item_id=r.id
            LEFT JOIN raw_snapshot s ON s.id=f.latest_snapshot_id
            LEFT JOIN raw_snapshot_web w ON w.raw_snapshot_id=s.id WHERE r.kind='official_web' ORDER BY r.id'''
        for row in db.execute(query):
            body=dict(row);item_id=body.pop('id');body['raw_item_id']=item_id
            for old,new,default in [('reasons_json','reasons',[]),('time_evidence_json','time_evidence',[]),('attachments_json','attachments',[]),('images_json','images',[])]:
                body[new]=json.loads(body.pop(old) or json.dumps(default))
            items.append({'id':item_id,'canonical_url':body['canonical_url'],'body':body,
                'content_hash':body.get('content_hash') or '', 'title_hash':body.get('title_hash') or '',
                'display_group':body.get('display_group') or item_id})
        item_ids={row['id'] for row in items}
        refs=[{'item_id':row['raw_item_id'],'source_id':row['source_id'],'first_seen_at':row['first_seen_at'],'last_seen_at':row['last_seen_at']}
              for row in db.execute('SELECT * FROM official_item_source') if row['raw_item_id'] in item_ids]
        aliases=[{'url':row['url'],'item_id':row['raw_item_id']} for row in db.execute('SELECT * FROM official_url_alias') if row['raw_item_id'] in item_ids]
        notifications=[]
        columns=['id','channel','notification_type','item_group','dedup_key','status','created_at','scheduled_at','attempted_at',
                 'request_started_at','sent_at','failed_at','attempt_count','last_error','suppressed_reason','provider_message_id']
        for row in db.execute('SELECT * FROM official_notification ORDER BY id'):
            if row['raw_item_id'] not in item_ids:continue
            event={key:row[key] for key in columns}
            event['item_id']=row['raw_item_id'];event['metadata']=json.loads(row['payload_metadata'])
            if event['status'] in {'pending','sending'} or event['status']=='failed' and event['scheduled_at']:
                event['metadata']['migration_original_status']=event['status']
                event.update(status='suppressed',scheduled_at=None,suppressed_reason='historical_migration')
            notifications.append(event)
        existing={row['item_group'] for row in notifications if row['channel']=='bark'}
        for item in items:
            if item['display_group'] in existing:continue
            dedup='bark:new_item:'+item['display_group'];existing.add(item['display_group'])
            notifications.append({'id':hashlib.sha256(dedup.encode()).hexdigest(),'channel':'bark','notification_type':'new_item',
                'item_id':item['id'],'item_group':item['display_group'],'dedup_key':dedup,'status':'suppressed',
                'created_at':activation,'scheduled_at':None,'attempted_at':None,'request_started_at':None,
                'sent_at':None,'failed_at':None,'attempt_count':0,'last_error':None,'suppressed_reason':'historical_migration',
                'provider_message_id':None,'metadata':{}})
        runs=[]
        for row in db.execute('SELECT * FROM official_run ORDER BY started_at DESC LIMIT 1000'):
            runs.append({'id':row['id'],'batch_id':row['batch_id'],'source_id':row['source_id'],'started_at':row['started_at'],
                'finished_at':row['finished_at'],'status':row['status'],
                'counts':{key:row[key] for key in ('scanned','discovered','inserted','updated','skipped','failed','requests')},'error_code':row['error_code']})
        meta={'id':1,'schema_version':1,'database_id':identity,'activation_at':activation,
              'policy':NotificationPolicyConfig.model_validate(json.loads(prefs['policy_json'])).model_dump(),
              'enabled':bool(prefs['enabled']),'last_success_at':prefs['last_success_at'],
              'last_failure_at':prefs['last_failure_at'],'last_error':prefs['last_error']}
        payload={'meta':meta,'sources':sources,'items':items,'source_refs':refs,'aliases':aliases,
                 'notification_events':notifications,'crawl_runs':runs}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    payload['meta']['snapshot_hash']=digest
    target=destination/'school-data.json'
    target.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    report={'backup':str(backup),'export':str(target),'database_id':identity,'activation_at':activation,
        'integrity_check':'ok','counts':{key:len(value) for key,value in payload.items() if isinstance(value,list)},
        'export_bytes':target.stat().st_size,'source_sqlite_bytes':Path(database).stat().st_size}
    (destination/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def validate_payload(payload):
    digest=payload['meta'].pop('snapshot_hash')
    try:
        actual=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    finally:payload['meta']['snapshot_hash']=digest
    if actual!=digest:raise ValueError('导出校验和不匹配。')
    return {key:len(value) for key,value in payload.items() if isinstance(value,list)}


def import_snapshot(database,payload):
    counts=validate_payload(payload)
    with database.connection() as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtext('zqu-import-once'))")
        prior=db.execute('SELECT * FROM zqu_worker.meta WHERE id=1').fetchone()
        if prior:
            if prior['snapshot_hash']==payload['meta']['snapshot_hash']:
                return {'already_imported':True,'database_id':prior['database_id']}
            raise ValueError('目标已有生产身份，拒绝覆盖。')
        for table in ['sources','items','source_refs','aliases','notification_events','crawl_runs']:
            for row in payload[table]:
                columns=list(row)
                values=[Jsonb(row[key]) if isinstance(row[key],dict) else row[key] for key in columns]
                # Table/columns come only from the validated fixed export contract below.
                allowed={'sources':{'id','config','state','next_check_at'},'items':{'id','canonical_url','body','content_hash','title_hash','display_group'},
                    'source_refs':{'item_id','source_id','first_seen_at','last_seen_at'},'aliases':{'url','item_id'},
                    'notification_events':{'id','channel','notification_type','item_id','item_group','dedup_key','status','created_at','scheduled_at',
                    'attempted_at','request_started_at','sent_at','failed_at','attempt_count','last_error','suppressed_reason','provider_message_id','metadata'},
                    'crawl_runs':{'id','batch_id','source_id','started_at','finished_at','status','counts','error_code'}}
                if set(columns)!=allowed[table]:raise ValueError('导出字段不匹配。')
                from psycopg import sql
                db.execute(sql.SQL('INSERT INTO zqu_worker.{} ({}) VALUES ({})').format(sql.Identifier(table),
                    sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns)),values)
            actual=db.execute('SELECT count(*) AS n FROM zqu_worker.'+table).fetchone()['n']
            if actual!=counts[table]:raise ValueError('迁移数量验证失败。')
        meta=payload['meta'];columns=list(meta)
        if set(columns)!={'id','schema_version','database_id','activation_at','policy','enabled','last_success_at','last_failure_at','last_error','snapshot_hash'}:
            raise ValueError('生产基线字段不匹配。')
        from psycopg import sql
        db.execute(sql.SQL('INSERT INTO zqu_worker.meta ({}) VALUES ({})').format(sql.SQL(',').join(map(sql.Identifier,columns)),
            sql.SQL(',').join(sql.Placeholder() for _ in columns)),[Jsonb(meta[c]) if isinstance(meta[c],dict) else meta[c] for c in columns])
    return {'counts':counts,'database_id':meta['database_id'],'activation_at':meta['activation_at'],'committed':True}


def render_import_sql(payload):
    """One atomic INSERT-only import for a trusted SQL connector, without a DSN.

    The namespace must already be installed and all destination tables empty.
    Values are driver-quoted; neither article text nor export keys become SQL.
    The meta activation barrier is inserted last in the same atomic DO statement.
    """
    from psycopg import sql
    counts=validate_payload(payload)
    contract={
        'sources':['id','config','state','next_check_at'],
        'items':['id','canonical_url','body','content_hash','title_hash','display_group'],
        'source_refs':['item_id','source_id','first_seen_at','last_seen_at'],
        'aliases':['url','item_id'],
        'notification_events':['id','channel','notification_type','item_id','item_group','dedup_key','status','created_at',
            'scheduled_at','attempted_at','request_started_at','sent_at','failed_at','attempt_count','last_error',
            'suppressed_reason','provider_message_id','metadata'],
        'crawl_runs':['id','batch_id','source_id','started_at','finished_at','status','counts','error_code'],
        'meta':['id','schema_version','database_id','activation_at','policy','enabled','last_success_at','last_failure_at','last_error','snapshot_hash'],
    }
    if set(payload)!=set(contract):raise ValueError('导出表集合不匹配。')
    lines=["PERFORM pg_advisory_xact_lock(hashtext('zqu-import-once'));"]
    # Refuse to adopt partial or previously activated data. No overwrite/reset.
    for table in contract:
        lines.append(f"IF EXISTS(SELECT 1 FROM zqu_worker.{table}) THEN RAISE EXCEPTION 'School import target is not empty'; END IF;")
    for table,columns in contract.items():
        rows=[payload['meta']] if table=='meta' else payload[table]
        if any(set(row)!=set(columns) for row in rows):raise ValueError('导出字段不匹配。')
        literal=sql.Literal(json.dumps(rows,ensure_ascii=False)).as_string()
        names=','.join(columns)  # Fixed contract only; never supplied by the export.
        lines.append(f'INSERT INTO zqu_worker.{table}({names}) SELECT {names} FROM jsonb_populate_recordset(NULL::zqu_worker.{table},{literal}::jsonb);')
        expected=1 if table=='meta' else counts[table]
        lines.append(f"IF (SELECT count(*) FROM zqu_worker.{table})<>{expected} THEN RAISE EXCEPTION 'School import count mismatch'; END IF;")
    body='\n'.join(lines)
    delimiter='$zqu_import_'+hashlib.sha256(body.encode()).hexdigest()+'$'
    if delimiter in body:raise ValueError('导入 SQL 定界冲突。')
    return 'DO '+delimiter+' BEGIN\n'+body+'\nEND '+delimiter+';'


def render_import_batches(payload, *, max_bytes=256000):
    """Connector-size fallback: idempotent staging, exact rows, activation last.

    Every batch is atomic and insert-only. A matching existing row is accepted;
    conflicting data aborts that batch. No worker can run until the final meta
    insert validates every table count. Interrupted staging can safely resume.
    """
    from psycopg import sql
    counts=validate_payload(payload)
    # Reuse the strict fixed-contract validation before producing any statement.
    render_import_sql(payload)
    results=[]
    guard="PERFORM pg_advisory_xact_lock(hashtext('zqu-import-once'));\nIF EXISTS(SELECT 1 FROM zqu_worker.meta) THEN RAISE EXCEPTION 'School database already activated'; END IF;\n"
    def block(body):
        tag='$zqu_stage_'+hashlib.sha256(body.encode()).hexdigest()+'$'
        if tag in body:raise ValueError('导入 SQL 定界冲突。')
        return 'DO '+tag+' BEGIN\n'+guard+body+'\nEND '+tag+';'
    def statement(table,rows):
        names=','.join(rows[0])  # Keys validated against the fixed contract above.
        record=f'jsonb_populate_recordset(NULL::zqu_worker.{table},'+sql.Literal(json.dumps(rows,ensure_ascii=False)).as_string()+'::jsonb)'
        return block(f'INSERT INTO zqu_worker.{table}({names}) SELECT {names} FROM {record} ON CONFLICT DO NOTHING;\n'
            f"IF EXISTS(SELECT {names} FROM {record} EXCEPT SELECT {names} FROM zqu_worker.{table}) THEN RAISE EXCEPTION 'School import row mismatch'; END IF;")
    for table in ['sources','items','source_refs','aliases','notification_events','crawl_runs']:
        batch=[]
        for row in payload[table]:
            candidate=batch+[row]
            if batch and len(statement(table,candidate).encode())>max_bytes:
                results.append(statement(table,batch));batch=[row]
            else:batch=candidate
        if batch:results.append(statement(table,batch))
    checks='\n'.join(f"IF (SELECT count(*) FROM zqu_worker.{table})<>{count} THEN RAISE EXCEPTION 'School import count mismatch'; END IF;" for table,count in counts.items())
    meta=payload['meta'];names=','.join(meta)
    final=checks+'\n'+f'INSERT INTO zqu_worker.meta({names}) SELECT {names} FROM jsonb_populate_recordset(NULL::zqu_worker.meta,'+sql.Literal(json.dumps([meta],ensure_ascii=False)).as_string()+'::jsonb);'
    results.append(block(final))
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['export','validate','import','render-sql','render-batches'])
    parser.add_argument('--database');parser.add_argument('--destination');parser.add_argument('--snapshot')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if args.action=='export':
        if not args.database or not args.destination:parser.error('export requires database and destination')
        print(json.dumps(export_snapshot(args.database,args.destination)))
        return
    payload=json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
    counts=validate_payload(payload)
    if args.action=='render-batches':
        if not args.destination:parser.error('render-batches requires a destination directory')
        destination=Path(args.destination);destination.mkdir(parents=True,exist_ok=True)
        batches=render_import_batches(payload)
        manifest=[]
        for index,statement in enumerate(batches):
            target=destination/f'{index:04d}.sql';target.write_text(statement,encoding='utf-8')
            manifest.append({'file':target.name,'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
        (destination/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        print(json.dumps({'batches':len(batches),'counts':counts,'database_id':payload['meta']['database_id']}));return
    if args.action=='render-sql':
        if not args.destination:parser.error('render-sql requires a destination file')
        Path(args.destination).write_text(render_import_sql(payload),encoding='utf-8')
        print(json.dumps({'sql_written':True,'counts':counts,'database_id':payload['meta']['database_id']}));return
    if args.action=='validate' or not args.apply:
        print(json.dumps({'dry_run':True,'counts':counts,'database_id':payload['meta']['database_id']}));return
    database=PostgresDatabase(os.environ['SUPABASE_DB_URL'])
    try:print(json.dumps(import_snapshot(database,payload)))
    finally:database.close()


if __name__=='__main__':main()
