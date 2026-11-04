"""GET-only review of existing API access boundaries; no session creation or upload."""
from datetime import datetime,timezone
import base64
import json
from pathlib import Path
import sys
import time

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.cloud_sync.settings import WorkerSettings,ENDPOINT
from server.personal_store import default_database_path


def main():
    out=ROOT/'artifacts/p0-20261104/final-scope-review'
    devices=json.loads((out/'device-session-evidence.json').read_text(encoding='utf-8'))
    config=WorkerSettings(default_database_path().with_name('cloud-worker-settings.json')).read()
    if not config or not config.get('enabled'):raise SystemExit('existing_processor_unavailable')
    def b64(value):return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    forged='.'.join((b64({'alg':'HS256','typ':'JWT'}),b64({'sub':devices['owner']['owner_id'],
        'session_id':devices['devices'][0]['session_id'],'role':'authenticated','exp':int(time.time())+60}),
        b64({'not_a_valid_signature':True})))
    checks=[]
    cases=[('public_health','/health',{},200),
        ('anonymous_inbox','/inbox?scope=all&status=all',{},401),
        ('forged_owner_jwt','/inbox?scope=all&status=all',{'Authorization':'Bearer '+forged},401),
        ('processor_is_not_owner_session','/inbox?scope=all&status=all',{'X-Eric-Worker-Key':config['worker_key']},401),
        ('anonymous_raw_review','/raw/5670fdb2-c494-401d-8108-85d99c46aa5c',{},401),
        ('forged_file_ticket','/files/review-only-file/content?ticket=invalid.invalid',{},401),
        ('invalid_processor','/worker/records?offset=0',{'X-Eric-Worker-Key':'ework_'+'x'*43},401),
        ('existing_processor_metadata','/worker/records?offset=0',{'X-Eric-Worker-Key':config['worker_key']},200)]
    with httpx.Client(timeout=20,follow_redirects=False) as client:
        for name,path,headers,expected in cases:
            response=client.get(ENDPOINT+path,headers=headers)
            item={'name':name,'method':'GET','status':response.status_code,'expected':expected,'PASS':response.status_code==expected}
            if name=='existing_processor_metadata' and response.status_code==200:
                rows=response.json()['items']
                item['metadata_only']=all(set(r)<={'kind','id','source_hash'} for r in rows)
                item['PASS']=item['PASS'] and item['metadata_only']
            checks.append(item)
    result={'at':datetime.now(timezone.utc).isoformat(),'checks':checks,'PASS':all(c['PASS'] for c in checks),
        'cloud_content_writes':0,'device_or_session_writes':0,'model_requests':0,
        'limit':'No valid owner JWT was extracted or replayed. Positive owner-session access is established by current ownership/session metadata and deployed-code contract tests, not a fresh login on each physical device.'}
    (out/'live-read-access-checks.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['PASS'] else 1


if __name__=='__main__':raise SystemExit(main())
