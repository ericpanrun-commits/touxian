"""Independent future-notice consumer. Default check is local and read-only.

No approval exists by default. --activate requires separately recorded consent
AND a fresh independently verified deployment proof. It does not start a worker.
Neither the seven-text test nor the one-notice cloud test grants this permission.
"""
import argparse
import asyncio
from contextlib import ExitStack,closing
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

import httpx
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.cloud_store import ProcessLock
from server.cloud_sync.deepseek_cloud_scope import CloudSource,ContinuousCloudGrant,instant
from server.cloud_sync.deepseek_cloud_continuous import continuous_approval,ContinuousLocalEvidence
from server.cloud_sync.deepseek_cloud_consumer import ConsumerState,DeepSeekCloudConsumer
from server.cloud_sync.deepseek_cloud_evidence import EVIDENCE_FILES,evidence_digest
from server.cloud_sync.deepseek_dispatch import ReviewedDeepSeekDispatch
from server.cloud_sync.wechat_dispatch_authority import DispatchAuthority,DispatchDenied,Grant
from server.cloud_sync.wechat_dispatch_transport import DispatchTransport
from server.cloud_sync.wechat_file_lease import immutable_evidence
from server.cloud_sync.wechat_incremental_journal import WeChatIncrementalJournal,JournalError
from server.cloud_sync.wechat_prepared_proxy import PreparedLocalProxyBackend
from server.cloud_sync.settings import WorkerSettings,ENDPOINT
from server.wechat_collect.config import SettingsDocument
from server.wechat_collect.deepseek_content_gate import read_grant,FILENAME,binding_digest

MANIFEST=ROOT/'patches/deepseek-cloud-qr-v25-candidate-20261108.json'
OWNER_RECEIPT=ROOT/'artifacts/p0-20261104/limited-upload/execution-receipt.json'
PROOF=ROOT/'artifacts/deepseek-cloud-continuous-20261108/deployment-proof.json'
PROXY='http://127.0.0.1:7897'
PRIOR_RECORDS={'call_352b35dc67624000ad6e1472ec5cdd82','call_8185e266de77402aadb066fd8ac9d958',
               'call_b332c086dbd5481fa7ab1fadf90e9a75'}
REQUIRED=('approval.json','release.json','authority.sqlite3','journal.json','runtime.sqlite3')


def now():return datetime.now(timezone.utc)
def digest(raw):return hashlib.sha256(raw).hexdigest()
def require(value,reason):
    if value is not True:raise DispatchDenied(reason)
def cache_for(directory):return Path(directory).resolve()/'cloud-sync/deepseek-continuous'


def implementation_hash():
    names=['cloud_sync/'+name+'.py' for name in ('deepseek_cloud_scope','deepseek_cloud_continuous',
        'deepseek_cloud_consumer','deepseek_cloud_evidence','deepseek_dispatch','deepseek_preview',
        'wechat_incremental_journal','wechat_dispatch_authority','wechat_dispatch_transport',
        'wechat_prepared_connection','wechat_prepared_proxy','wechat_file_lease','wechat_outbound_validation',
        'minimization','minimized_projection','settings')]
    names+=['wechat_collect/'+name+'.py' for name in ('analysis','models','json_output','deepseek_content_gate',
        'deepseek_projection','qr_text_policy','quality','config')]
    names+=['source_metadata.py','user_context.py','personal_store.py','cloud_store.py']
    hashes={name:digest((ROOT/'server'/name).read_bytes()) for name in names}
    hashes['entrypoint']=digest(Path(__file__).read_bytes())
    hashes['api_manifest']=digest(MANIFEST.read_bytes())
    return digest(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode())


def verify_proof(proof,manifest,owner,at):
    require(0<=(instant(at)-instant(proof['checked_at'])).total_seconds()<=900,'remote_preflight_stale')
    require(proof['endpoint']==ENDPOINT and type(proof['api_version']) is int and proof['api_version']>24
        and proof['api_files']==manifest['files'],'new_guarded_api_not_verified')
    require(proof['implementation_hash']==implementation_hash(),'implementation_changed')
    require(owner['outcome']=='published_and_replayed' and proof['processor_revoked'] is False
        and proof['workspace_id']==owner['workspace_id'] and proof['processor_id']==owner['processor_id']
        and proof['publisher_epoch']==owner['epoch'] and type(proof['generation']) is int
        and proof['generation']==1,'publisher_ownership_unverified')
    require(proof['owner_receipt_sha256']==digest(OWNER_RECEIPT.read_bytes()),'owner_receipt_changed')


def write_new(path,value):
    with path.open('xb') as output:
        output.write(json.dumps(value,sort_keys=True,indent=2).encode())
        output.flush();os.fsync(output.fileno())


def activate(directory,record,approved_at):
    require(isinstance(record,str) and bool(record.strip()) and record not in PRIOR_RECORDS,
        'separate_continuous_cloud_approval_required')
    require(approved_at is not None and instant(approved_at)<=now(),'approval_time_required')
    directory=Path(directory).resolve();cache=cache_for(directory)
    require(not cache.exists(),'activation_already_present')
    with ExitStack() as stack:
        proof=json.loads(stack.enter_context(immutable_evidence(PROOF,max_bytes=16384)))
        manifest=json.loads(stack.enter_context(immutable_evidence(MANIFEST,max_bytes=16384)))
        owner=json.loads(stack.enter_context(immutable_evidence(OWNER_RECEIPT,max_bytes=65536)))
        verify_proof(proof,manifest,owner,now())
        files={name:stack.enter_context(immutable_evidence(directory/name,max_bytes=65536)) for name in EVIDENCE_FILES}
        controls=json.loads(files['recovery-controls.json'])
        require(controls.get('version')==1 and controls.get('hold_cloud_uploads') is True
            and controls.get('pause_paid_ai') is True,'global_holds_required')
        deepseek=read_grant(files[FILENAME])
        document=SettingsDocument.model_validate(yaml.safe_load(files['wechat.yaml']))
        config=WorkerSettings(directory/'cloud-worker-settings.json').read()
        require(bool(config) and config['enabled'] is True and config['workspace_id']==proof['workspace_id']
            and config['processor_id']==proof['processor_id'],'cloud_connection_changed')
        ids=set(deepseek.chat_watermarks)
        require(deepseek.mode=='continuous' and len(ids)==5 and document.wechat.enabled
            and ids=={c.id for c in document.wechat.priority_chats if c.enabled and document.wechat.allows(c)},
            'five_current_sources_required')
        # This acquires a short source transaction, with no production writes.
        with closing(sqlite3.connect((directory/'personal.sqlite3').as_uri()+'?mode=rw',uri=True,timeout=.5)) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
            at=now();sources=[]
            for chat in sorted(ids):
                row=db.execute('SELECT * FROM wechat_chat WHERE id=?',(chat,)).fetchone()
                require(row is not None and bool(row['enabled']) and row['source_id']=='wechat:'+chat,
                    'source_binding_unavailable')
                binding=binding_digest(row['source_id'],row['binding_json'])
                require(deepseek.source_bindings[chat]==binding,'source_binding_changed')
                seq=db.execute('SELECT COALESCE(MAX(ingest_seq),0) FROM wechat_message WHERE chat_id=?',(chat,)).fetchone()[0]
                sources.append(CloudSource(chat,row['source_id'],binding,seq))
            scope=ContinuousCloudGrant(str(uuid4()),instant(approved_at).isoformat(),at.isoformat(),
                proof['workspace_id'],proof['processor_id'],proof['publisher_epoch'],proof['generation'],
                evidence_digest(files),proof['api_bundle_hash'],tuple(sources))
            cache.mkdir(parents=True,exist_ok=False)
            # Any partial failure deliberately leaves an incomplete, unusable
            # activation. A future start cannot silently recreate missing files.
            approval={'version':1,'mode':'continuous','enabled':True,'authorization_record':record,'scope':asdict(scope)}
            write_new(cache/'approval.json',approval)
            write_new(cache/'release.json',{'version':1,'implementation_hash':implementation_hash(),
                'approval_sha256':digest((cache/'approval.json').read_bytes()),'proof':proof})
            authority=DispatchAuthority(cache/'authority.sqlite3');authority.initialize()
            grant=Grant(scope.approval_id,scope.binding(),instant(scope.expires_at).timestamp(),False)
            authority.import_verified(grant,lambda value:value==grant and continuous_approval((cache/'approval.json').read_bytes())==scope)
            lock=ProcessLock(cache);lock.acquire()
            try:
                journal=WeChatIncrementalJournal(cache/'journal.json',lock)
                journal._save(journal._read())
                ConsumerState(cache/'runtime.sqlite3',scope).initialize()
            finally:lock.close()
            db.rollback()
    return {'state':'activated_not_started','sources':5,'history_backfill':False,'not_before':scope.not_before}


def load(directory):
    cache=cache_for(directory)
    require(all((cache/name).is_file() for name in REQUIRED),'continuous_cloud_authorization_required')
    raw=(cache/'approval.json').read_bytes();scope=continuous_approval(raw)
    release=json.loads((cache/'release.json').read_bytes());proof=release['proof']
    require(release['version']==1 and release['approval_sha256']==digest(raw)
        and release['implementation_hash']==implementation_hash(),'release_changed')
    manifest=json.loads(MANIFEST.read_bytes())
    api_matches=proof['api_bundle_hash']==scope.api_bundle_hash or (
        manifest.get('baseline_bundle_sha256')==scope.api_bundle_hash
        and proof.get('authorization_api_bundle_hash')==scope.api_bundle_hash)
    require(proof['endpoint']==ENDPOINT and proof['api_files']==manifest['files']
        and api_matches and proof['workspace_id']==scope.workspace_id
        and proof['processor_id']==scope.processor_id and proof['publisher_epoch']==scope.epoch
        and proof['generation']==scope.generation,'release_scope_changed')
    return cache,scope,release


def check(directory):
    cache,scope,release=load(directory)
    local=ContinuousLocalEvidence(directory,cache/'approval.json',approval_sha256=release['approval_sha256'])
    with local.files(scope):pass
    authority=DispatchAuthority(cache/'authority.sqlite3')
    with closing(sqlite3.connect(authority.path.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;authority._grant(db,scope.approval_id,scope.binding())
    return {'state':'ready','sources':5,'network_requests':0}


def revoke(directory):
    # Revocation must still work after a code/config change. Do not use load().
    cache=cache_for(directory);scope=continuous_approval((cache/'approval.json').read_bytes())
    DispatchAuthority(cache/'authority.sqlite3').revoke(scope.approval_id)
    return {'state':'revoked','already_sent_bytes_recalled':False}


async def run(directory,once=False):
    check(directory)
    cache,scope,release=load(directory);lock=ProcessLock(cache);lock.acquire()
    try:
        authority=DispatchAuthority(cache/'authority.sqlite3')
        journal=WeChatIncrementalJournal(cache/'journal.json',lock)
        local=ContinuousLocalEvidence(directory,cache/'approval.json',approval_sha256=release['approval_sha256'])
        release_hash=digest((cache/'release.json').read_bytes())
        def current_release():
            return (digest((cache/'release.json').read_bytes())==release_hash
                and implementation_hash()==release['implementation_hash'])
        transport=DispatchTransport(PreparedLocalProxyBackend('https://mantuehuzptactymjpqg.supabase.co',PROXY))
        async with httpx.AsyncClient(transport=transport,follow_redirects=False) as client, \
                httpx.AsyncClient(proxy=PROXY,trust_env=False,follow_redirects=False,timeout=10) as metadata:
            async def status_read(config):
                response=await metadata.get(ENDPOINT+'/worker/wechat-status',headers={'X-Eric-Worker-Key':config['worker_key']})
                require(response.status_code==200,'publisher_status_unavailable')
                require(len(response.content)<=4096,'publisher_status_invalid')
                return response.json()
            def factory(current):
                return ReviewedDeepSeekDispatch(journal=journal,authority=authority,transport=transport,client=client,
                    protected_preview=local.snapshot,current=current,clock=now)
            consumer=DeepSeekCloudConsumer(scope=scope,state=ConsumerState(cache/'runtime.sqlite3',scope),local=local,
                authority=authority,journal=journal,status_read=status_read,sender_factory=factory,clock=now,release_current=current_release)
            while True:
                try:result=await consumer.tick()
                except DispatchDenied as error:
                    # No content/user data is part of these fixed codes.
                    result={'state':str(error)}
                    if str(error)!='publisher_status_unavailable':raise
                except JournalError as error:
                    if str(error) not in {'daily_limit_reached','cycle_limit_reached'}:raise
                    result={'state':str(error)}
                except httpx.TransportError:result={'state':'metadata_network_unavailable'}
                status={'at':now().isoformat(),'pid':os.getpid(),'full_cloud_sync_enabled':False,**result}
                temporary=cache/'status.tmp'
                temporary.write_text(json.dumps(status),encoding='utf8');temporary.replace(cache/'status.json')
                print(json.dumps(status),flush=True)
                if once:return result
                await asyncio.sleep(60)
    finally:lock.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=ROOT.parent/'data/attention-desk')
    modes=parser.add_mutually_exclusive_group()
    for mode in ('activate','run','once','revoke'):modes.add_argument('--'+mode,action='store_true')
    parser.add_argument('--authorization-record');parser.add_argument('--approved-at')
    args=parser.parse_args()
    try:
        if args.activate:result=activate(args.directory,args.authorization_record,args.approved_at)
        elif args.revoke:result=revoke(args.directory)
        elif args.run or args.once:result=asyncio.run(run(args.directory,args.once))
        else:result=check(args.directory)
    except DispatchDenied as error:result={'state':str(error)}
    except Exception:result={'state':'local_preflight_unavailable'}
    print(json.dumps(result))
    return 0 if result['state'] in {'ready','activated_not_started','revoked','idle','checked'} else 2


if __name__=='__main__':raise SystemExit(main())
