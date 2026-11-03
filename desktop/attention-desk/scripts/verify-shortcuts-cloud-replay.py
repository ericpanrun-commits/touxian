"""Replay an already-ACKed original with its original receipt; never create a learner fixture."""
import argparse
import asyncio
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.notes_anywhere.settings import NotesSettings,ENDPOINT
from server.cloud_sync.settings import WorkerSettings,ENDPOINT as APP_ENDPOINT


async def main(args):
    source=Path(args.database).resolve();connection=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True);connection.row_factory=sqlite3.Row
    row=connection.execute('SELECT * FROM attachment WHERE sha256=? AND local_path IS NOT NULL LIMIT 1',(args.sha256,)).fetchone()
    receipt=connection.execute('SELECT * FROM inbox_receipt WHERE request_id=?',(args.receipt,)).fetchone()
    assert row and receipt,'Original and prior local receipt are required'
    before=connection.execute('SELECT count(*) FROM raw_item').fetchone()[0]
    body=Path(row['local_path']).read_bytes();assert hashlib.sha256(body).hexdigest()==args.sha256
    filename=row['filename'][65:] if row['filename'].startswith(args.sha256+'-') else row['filename']
    config=NotesSettings(source.with_name('notes-anywhere-settings.json')).read();worker=WorkerSettings(args.settings).read()
    sync_headers={'X-Eric-Sync-Key':config['sync_key']};device=None;result=None;revoked=False
    async with httpx.AsyncClient(timeout=90,follow_redirects=False) as client:
        try:
            response=await client.post(ENDPOINT+'/devices',headers=sync_headers,json={})
            assert response.status_code==201,f'Temporary device registration: {response.status_code}'
            device=response.json()
            for attempt in range(2):
                response=await client.post(ENDPOINT+'/upload',headers={'X-Eric-Notes-Key':device['upload_key'],'Content-Type':'application/pdf','X-Eric-Filename':quote(filename,safe=''),'X-Eric-Shared-At':receipt['created_at']},content=body)
                assert response.status_code in (200,202),f'Original replay failed: {response.status_code}'
                result=response.json();assert result.get('saved') and result.get('received') and result['receipt_id']==args.receipt and result.get('duplicate')
            resource='capture:'+args.receipt+':'+args.sha256
            response=await client.get(APP_ENDPOINT+'/worker/files/'+quote(resource,safe='')+'/content',headers={'X-Eric-Worker-Key':worker['worker_key']})
            assert response.status_code==200 and response.content==body,'Permanent cloud original differs'
        finally:
            if device:
                response=await client.post(ENDPOINT+'/devices/revoke',headers=sync_headers,json={'id':device['device_id']})
                revoked=response.status_code==200 and response.json().get('revoked') is True
                assert revoked,'Temporary probe device must be revoked'
    after=connection.execute('SELECT count(*) FROM raw_item').fetchone()[0];connection.close();assert after==before,'Replay must not create another local original'
    report={'passed':True,'receipt_id':args.receipt,'same_original_receipt':True,'duplicate_replays':2,'sha256':args.sha256,'bytes':len(body),'permanent_cloud_copy_matches':True,'temporary_device_revoked':revoked,'local_raw_count_before':before,'local_raw_count_after':after,'checked_at':datetime.now(timezone.utc).isoformat()}
    Path(args.report).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'same_receipt':True,'permanent_copy_verified':True,'local_duplicates':0,'probe_device_revoked':revoked}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True);parser.add_argument('--settings',required=True);parser.add_argument('--receipt',required=True);parser.add_argument('--sha256',required=True);parser.add_argument('--report',required=True)
    asyncio.run(main(parser.parse_args()))
