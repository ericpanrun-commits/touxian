"""Read-only inventory of existing Eric OS projections; never uploads content."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.cloud_sync.settings import WorkerSettings, ENDPOINT
from server.cloud_sync.projection import CloudProjection
from server.personal_store import PersonalStore, default_database_path


def interval(values):
    dates=[]
    for value in values:
        if value:
            try: dates.append(datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc))
            except (ValueError,TypeError): pass
    return {'from':min(dates).isoformat() if dates else None,'through':max(dates).isoformat() if dates else None}


def main():
    output=ROOT/'artifacts/p0-20261104'
    personal=PersonalStore(default_database_path())
    config=WorkerSettings(personal.path.with_name('cloud-worker-settings.json')).read()
    if not config or not config.get('enabled'):raise SystemExit('Existing cloud catalogue unavailable')
    cloud={};resources={};read_requests=0
    with httpx.Client(timeout=30,follow_redirects=False,headers={'X-Eric-Worker-Key':config['worker_key']}) as client:
        for name,destination in (('records',cloud),('resources',resources)):
            offset=0
            while True:
                response=client.get(ENDPOINT+'/worker/'+name+'?offset='+str(offset));read_requests+=1
                response.raise_for_status();page=response.json()
                for row in page['items']:
                    destination[(row['kind'],row['id']) if name=='records' else row['id']]=row
                if len(page['items'])<page['limit']:break
                offset+=len(page['items'])
    projection=CloudProjection(personal,output/'inventory-projection')
    projected=projection.records()
    pending=[r for r in projected if cloud.get((r['kind'],r['id']),{}).get('source_hash')!=r['source_hash']]
    with closing(sqlite3.connect(projection.cache/'projection.sqlite3')) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
        chats=[dict(r) for r in c.execute('SELECT * FROM wechat_chat WHERE enabled=1 ORDER BY id')]
        raw_rows=[dict(r) for r in c.execute('SELECT * FROM raw_item')]
        raw_sources={r['id']:r['source_id'] for r in raw_rows}
        raw_sources.update({r['raw_item_id']:r['source_id'] for r in c.execute('SELECT b.raw_item_id,ch.source_id FROM wechat_batch b JOIN wechat_chat ch ON ch.id=b.chat_id WHERE b.raw_item_id IS NOT NULL')})
        snapshots={r['id']:dict(r) for r in c.execute('SELECT * FROM raw_snapshot')}
        attachments=[dict(r) for r in c.execute('SELECT * FROM attachment')]
        attachment_sources={a['id']:a['raw_item_id'] for a in attachments}
        document_sources={r['id']:attachment_sources.get(r['attachment_id']) for r in c.execute('SELECT id,attachment_id FROM document')}
        def raw_for(record):
            kind,identity,payload=record['kind'],record['id'],record['payload']
            if kind in {'raw_item','inbox_item','inbox'}:return identity
            if kind in {'raw_review','snapshot'}:return snapshots.get(identity,{}).get('raw_item_id')
            if kind=='document':return document_sources.get(identity)
            if kind in {'document_page','document_section'}:return document_sources.get(payload.get('document_id'))
            return payload.get('raw_item_id')
        record_raw={(r['kind'],r['id']):raw_for(r) for r in projected}
        source_rows=[];raw_manifest=[];attachment_manifest=[]
        patterns={'phone':r'(?<!\d)1[3-9]\d{9}(?!\d)','identity_number':r'(?<!\d)\d{17}[\dXx](?!\d)',
                  'email':r'[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', 'wechat_identifier':r'wxid_[A-Za-z0-9_]+'}
        for chat in chats:
            raw_ids={identity for identity,source in raw_sources.items() if source==chat['source_id']}
            source_pending=[r for r in pending if record_raw[(r['kind'],r['id'])] in raw_ids]
            pending_raw={record_raw[(r['kind'],r['id'])] for r in source_pending}
            pending_snapshots=[s for s in snapshots.values() if s['raw_item_id'] in pending_raw]
            source_attachments=[a for a in attachments if a['raw_item_id'] in raw_ids]
            pending_files=[];not_downloaded=[]
            for attachment in source_attachments:
                identity='attachment:'+attachment['id']+':original'
                if not attachment['local_path']:
                    not_downloaded.append(attachment['id']);continue
                path=Path(attachment['local_path'])
                actual_sha=None;error=None;size=None
                try:
                    size=path.stat().st_size
                    with path.open('rb') as stream:actual_sha=hashlib.file_digest(stream,'sha256').hexdigest()
                    if attachment['sha256'] and actual_sha!=attachment['sha256']:error='hash_mismatch'
                except OSError:error='local_original_unavailable'
                acknowledged=resources.get(identity)
                if acknowledged and acknowledged.get('sha256')==actual_sha and acknowledged.get('bytes')==size:continue
                item={'source_id':chat['source_id'],'attachment_id':attachment['id'],'raw_item_id':attachment['raw_item_id'],
                    'resource_id':identity,'filename':attachment['filename'],'mime':attachment['mime_type'],
                    'bytes':size,'sha256':actual_sha,'created_at':attachment['created_at'],'verification_error':error,
                    'cloud_identity_conflict':bool(acknowledged),'sensitivity':'unreviewed_sensitive_by_default','approved':False}
                pending_files.append(item);attachment_manifest.append(item)
            messages=[dict(r) for r in c.execute('SELECT * FROM wechat_message WHERE chat_id=?',(chat['id'],))]
            batch_messages=[dict(r) for r in c.execute('SELECT bm.batch_id,m.id,m.source_time,m.sender FROM wechat_batch_message bm JOIN wechat_message m ON m.id=bm.message_id JOIN wechat_batch b ON b.id=bm.batch_id WHERE b.chat_id=?',(chat['id'],))]
            pending_batches={r['id']:r['raw_item_id'] for r in c.execute('SELECT id,raw_item_id FROM wechat_batch WHERE chat_id=?',(chat['id'],)) if r['raw_item_id'] in pending_raw}
            included_messages={m['id']:m for m in batch_messages if m['batch_id'] in pending_batches}
            text='\n'.join(s['original_text'] for s in pending_snapshots)
            pii={name:len(re.findall(pattern,text)) for name,pattern in patterns.items()}
            pii['sender_identifiers_in_message_provenance']=len({m['sender'] for m in included_messages.values() if m['sender']})
            for raw_id in sorted(pending_raw):
                rows=[r for r in source_pending if record_raw[(r['kind'],r['id'])]==raw_id]
                stamps=[m['source_time'] for m in batch_messages if pending_batches.get(m['batch_id'])==raw_id]
                raw_manifest.append({'source_id':chat['source_id'],'raw_item_id':raw_id,'source_time':interval(stamps),
                    'records':[{'kind':r['kind'],'id':r['id'],'source_hash':r['source_hash'],
                        'change':'update' if (r['kind'],r['id']) in cloud else 'new'} for r in rows],
                    'approved':False})
            source_rows.append({'source_id':chat['source_id'],'name':chat['name'],'chat_id':chat['id'],
                'local_messages':len(messages),'local_raw':len(raw_ids),'pending_raw':len(pending_raw),
                'pending_raw_new':sum(('raw_item',identity) not in cloud for identity in pending_raw),
                'pending_raw_changed':sum(('raw_item',identity) in cloud for identity in pending_raw),
                'pending_projection_records':len(source_pending),'projection_kinds':dict(Counter(r['kind'] for r in source_pending)),
                'pending_message_provenance_count':len(included_messages),'pending_message_time_range':interval(m['source_time'] for m in included_messages.values()),
                'pending_raw_snapshot_time_range':interval(s['source_time'] for s in pending_snapshots),
                'local_message_time_range':interval(m['source_time'] for m in messages),
                'pending_attachment_files':len(pending_files),'pending_attachment_unique_hashes':len({a['sha256'] for a in pending_files if a['sha256']}),
                'pending_attachment_bytes':sum(a['bytes'] or 0 for a in pending_files),
                'pending_attachment_time_range':interval(a['created_at'] for a in pending_files),
                'attachment_references_not_downloaded':len(not_downloaded),
                'media_work_states':dict(Counter(r[0] for r in c.execute('SELECT w.status FROM wechat_media_work w JOIN wechat_message m ON m.id=w.message_id WHERE m.chat_id=?',(chat['id'],)))),
                'pii_likely':bool(pending_raw or pending_files),'pii_indicators':pii,
                'pii_review':'Text may contain personal names, sender identities, student/class information and third-party links. Regex counts are lower bounds; attachments need human review.'})
    result={'at':datetime.now(timezone.utc).isoformat(),'inventory_only':True,'cloud_content_writes':0,'model_requests':0,
        'catalogue_get_requests':read_requests,'cloud_known_records':len(cloud),'cloud_complete_resources':len(resources),
        'all_sources_pending_projection_records':len(pending),'all_sources_pending_kinds':dict(Counter(r['kind'] for r in pending)),
        'wechat_sources':source_rows,'raw_manifest':raw_manifest,'attachment_manifest':attachment_manifest,
        'pending_attachment_total':len(attachment_manifest),
        'pending_attachment_unique_hashes':len({a['sha256'] for a in attachment_manifest if a['sha256']}),
        'pending_attachment_total_bytes':sum(a['bytes'] or 0 for a in attachment_manifest),
        'pending_attachment_errors':sum(bool(a['verification_error'] or a['cloud_identity_conflict']) for a in attachment_manifest),
        'other_pending_records':[{'kind':r['kind'],'id':r['id'],'source_hash':r['source_hash'],
            'source_id':raw_sources.get(record_raw[(r['kind'],r['id'])])} for r in pending
            if raw_sources.get(record_raw[(r['kind'],r['id'])]) not in {ch['source_id'] for ch in chats}],
        'all_pending_facades':[{'id':r['id'],'source_hash':r['source_hash']} for r in pending if r['kind']=='facade'],
        'warning':'Existing sync publishes all sources/resources and aggregate facades. Do not lift the global hold before an approved source/date/field allowlist is enforced across every projection and file path.'}
    (output/'wechat-pending-sync-inventory.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:result[key] for key in ('at','inventory_only','cloud_content_writes','model_requests','all_sources_pending_projection_records','wechat_sources')},ensure_ascii=True))


if __name__=='__main__':main()
