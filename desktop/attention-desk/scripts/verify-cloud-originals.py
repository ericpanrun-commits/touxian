"""Read actual private cloud byte ranges; compare with unchanged source originals."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import quote
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.cloud_sync.projection import CloudProjection
from server.cloud_sync.settings import WorkerSettings,ENDPOINT
from server.personal_store import PersonalStore


async def main(args):
    source=PersonalStore(Path(args.database));projection=CloudProjection(source,Path(args.cache))
    resources,documents=projection.resources();selected=sorted(resources,key=lambda r:r['bytes'],reverse=True)[:2]
    note=next((r for r in resources if r['mime']=='application/pdf' and r['bytes']<5_000_000),None)
    if note:selected.append(note)
    settings=WorkerSettings(args.settings).read();reports=[]
    async with httpx.AsyncClient(timeout=90,follow_redirects=False) as client:
        for resource in selected:
            size=resource['bytes'];ranges=[(0,min(511,size-1)),(max(0,size-512),size-1)]
            if size>4_000_100:ranges.append((3_999_950,4_000_050))
            checks=[]
            for start,end in ranges:
                response=await client.get(ENDPOINT+'/worker/files/'+quote(resource['id'],safe='')+'/content',headers={'X-Eric-Worker-Key':settings['worker_key'],'Range':f'bytes={start}-{end}'})
                assert response.status_code==206,f'Range read failed: {response.status_code}'
                assert response.headers['Content-Range']==f'bytes {start}-{end}/{size}'
                with Path(resource['path']).open('rb') as stream:stream.seek(start);expected=stream.read(end-start+1)
                assert response.content==expected,'Original bytes differ'
                checks.append({'start':start,'end':end,'sha256':hashlib.sha256(expected).hexdigest(),'matches_original':True})
            reports.append({'resource_id':resource['id'],'bytes':size,'ranges':checks})
    report={'passed':True,'cloud_endpoint':ENDPOINT,'files':reports,'local_database_written':False,'windows_service_required':False}
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'originals':len(reports),'matched_ranges':sum(len(row['ranges']) for row in reports),'windows_service_required':False}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True);parser.add_argument('--cache',required=True);parser.add_argument('--settings',required=True);parser.add_argument('--report',required=True)
    asyncio.run(main(parser.parse_args()))
