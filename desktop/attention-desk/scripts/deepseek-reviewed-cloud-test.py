"""One explicitly approved notice; check by default, never a background uploader.

Authorization: call_352b35dc67624000ad6e1472ec5cdd82, 2026-11-08.
The user approved this exact projection and the corresponding guarded API
deployment. This file does not authorize any other notice or ongoing upload.
"""
import argparse
import asyncio
from contextlib import contextmanager,ExitStack
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.cloud_store import ProcessLock
from server.cloud_sync.deepseek_cloud_evidence import ReviewedLocalEvidence,cloud_approval,evidence_digest,EVIDENCE_FILES
from server.cloud_sync.deepseek_dispatch import ReviewedCloudGrant,ReviewedDeepSeekDispatch
from server.cloud_sync.deepseek_preview import build_deepseek_preview
from server.cloud_sync.settings import ENDPOINT
from server.cloud_sync.wechat_dispatch_authority import DispatchAuthority,DispatchDenied,Grant
from server.cloud_sync.wechat_dispatch_transport import DispatchTransport
from server.cloud_sync.wechat_file_lease import immutable_evidence
from server.cloud_sync.wechat_incremental_journal import WeChatIncrementalJournal,freeze_bundle
from server.cloud_sync.wechat_prepared_connection import PreparedBackend
from server.wechat_collect.deepseek_content_gate import read_grant,FILENAME

APPROVAL_RECORD='call_352b35dc67624000ad6e1472ec5cdd82'
BUNDLE='e5cf7276b19bfcf93d0bd152903af7e18448abd8e428af71fb0d11493b1aaadf'
RECEIPT='4e4f879f52626f5ea030a2f1038cf87185191a5c8c86b8ab55b246d002a827bb'
JOB='b3b115bf-a96d-4501-b1ca-0f73a8aa1f7a'
WORKSPACE='50e5a3ac-dd15-4f0a-a93c-ace4b735d16d'
PROCESSOR='bc5c2a70-d5c1-449c-b8c3-d72b8a02624d'
EPOCH='a4730c33-b6d2-4e29-a151-419971858135'
MANIFEST=ROOT/'patches/deepseek-cloud-api-v23-candidate-20261108.json'
OWNER_RECEIPT=ROOT/'artifacts/p0-20261104/limited-upload/execution-receipt.json'
PROOF=ROOT/'artifacts/deepseek-cloud-20261108/deployment-proof.json'


def now():return datetime.now(timezone.utc)
def digest(raw):return hashlib.sha256(raw).hexdigest()
def require(condition,reason):
    if not condition:raise DispatchDenied(reason)


def implementation_hash():
    # Pin the sender and source-validation code, without coupling unrelated UI,
    # Collector or official-site modules to this one-notice test.
    names=['cloud_sync/'+name+'.py' for name in ('deepseek_preview','deepseek_dispatch','deepseek_cloud_evidence',
        'wechat_incremental_journal','wechat_outbound_validation','wechat_dispatch_authority',
        'wechat_dispatch_transport','wechat_prepared_connection','wechat_file_lease','minimization','minimized_projection','settings')]
    names += ['wechat_collect/'+name+'.py' for name in ('deepseek_content_gate','deepseek_projection','analysis','models','json_output','config')]
    names += ['source_metadata.py','user_context.py','personal_store.py']
    hashes={name:digest((ROOT/'server'/name).read_bytes()) for name in names}
    hashes['script']=digest(Path(__file__).read_bytes())
    return digest(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode())


def verify_proof(proof,manifest,owner,at):
    age=(at-datetime.fromisoformat(proof['checked_at'])).total_seconds()
    require(0<=age<=900,'remote_preflight_stale')
    expected={**manifest['files'],'deno.json':manifest['baseline_files']['deno.json']}
    require(proof['endpoint']==ENDPOINT and type(proof['api_version']) is int and proof['api_version']>23
        and proof['api_files']==expected,'guarded_api_not_verified')
    require(proof['implementation_hash']==implementation_hash(),'implementation_changed')
    require(proof['workspace_id']==WORKSPACE and proof['processor_id']==PROCESSOR
        and proof['processor_revoked'] is False and proof['publisher_epoch']==EPOCH
        and type(proof['generation']) is int and proof['generation']==1,'publisher_scope_changed')
    require(owner['workspace_id']==WORKSPACE and owner['processor_id']==PROCESSOR
        and owner['epoch']==EPOCH and owner['outcome']=='published_and_replayed','publisher_ownership_unverified')
    require(proof['owner_receipt_sha256']==digest(OWNER_RECEIPT.read_bytes()),'owner_receipt_changed')
    require(isinstance(proof['api_bundle_hash'],str) and len(proof['api_bundle_hash'])==64
        and all(char in '0123456789abcdef' for char in proof['api_bundle_hash']),'api_bundle_unverified')


@contextmanager
def verified_remote():
    with ExitStack() as stack:
        raw=stack.enter_context(immutable_evidence(PROOF.resolve(),max_bytes=16384))
        manifest=stack.enter_context(immutable_evidence(MANIFEST.resolve(),max_bytes=16384))
        owner=stack.enter_context(immutable_evidence(OWNER_RECEIPT.resolve(),max_bytes=65536))
        proof=json.loads(raw)
        verify_proof(proof,json.loads(manifest),json.loads(owner),now())
        yield proof


def preview(directory):
    with ExitStack() as stack:
        files={name:stack.enter_context(immutable_evidence(directory/name,max_bytes=65536)) for name in EVIDENCE_FILES}
        controls=json.loads(files['recovery-controls.json'])
        require(controls.get('hold_cloud_uploads') is True and controls.get('pause_paid_ai') is True,'global_holds_required')
        connection=sqlite3.connect((directory/'personal.sqlite3').as_uri()+'?mode=ro',uri=True)
        connection.row_factory=sqlite3.Row
        try:
            connection.execute('PRAGMA query_only=ON');connection.execute('BEGIN')
            result=build_deepseek_preview(connection,read_grant(files[FILENAME]),JOB)
        finally:connection.rollback();connection.close()
        require(result['bundle']['bundle_hash']==BUNDLE and result['analysis_receipt_sha256']==RECEIPT
            and result['bundle']['record_count']==4 and result['bundle']['resources']==[],'reviewed_notice_changed')
        return result,evidence_digest(files)


class DisconnectAfterSend:
    """Exercise unknown delivery by closing this one prepared test connection."""
    def __init__(self,backend):self.backend=backend

    async def prepare(self,url):
        require(url.path.endswith('/worker/wechat-publish'),'disconnect_requires_first_publish')
        connection=await self.backend.prepare(url)
        class Interrupted:
            def accept(self,request):connection.accept(request)
            async def response(self):
                await connection.aclose()
                raise httpx.ReadError('controlled_disconnect_after_handoff')
            async def aclose(self):await connection.aclose()
        return Interrupted()

    async def aclose(self):await self.backend.aclose()


async def run(directory,*,execute=False,approval_record=None,revoke=False,disconnect_after_send=False):
    directory=Path(directory).resolve()
    # One fixed owner for every invocation. Do not borrow the desktop's lock or
    # release its full-upload hold. No cache/authority is created in check mode.
    cache=directory/'cloud-sync'/'deepseek-reviewed-test'
    approval=cache/'approval.json'
    authority=DispatchAuthority(cache/'authority.sqlite3',clock=lambda:now().timestamp())
    if revoke:
        grant=cloud_approval(approval.read_bytes())
        authority.revoke(grant.approval_id)
        return {'state':'revoked','already_sent_bytes_recalled':False}
    fresh,evidence=preview(directory)
    with verified_remote() as proof:
        if not execute:
            return {'state':'ready_for_authorized_test','records':4,'resources':0,'network_requests':0}
        require(approval_record==APPROVAL_RECORD,'explicit_approved_test_required')
        cache.mkdir(parents=True,exist_ok=True)
        lock=ProcessLock(cache);lock.acquire()
        try:
            journal=WeChatIncrementalJournal(cache/'journal.json',lock)
            present=[path.exists() for path in (approval,authority.path,journal.path)]
            require(all(present) or not any(present),'partial_activation_requires_review')
            if not any(present):
                at=now()
                grant=ReviewedCloudGrant(str(uuid4()),JOB,RECEIPT,BUNDLE,WORKSPACE,PROCESSOR,EPOCH,1,
                    at.isoformat(),(at+timedelta(hours=4)).isoformat(),evidence,proof['api_bundle_hash'],3)
                raw=json.dumps({'version':1,'approved':True,'authorization_record':APPROVAL_RECORD,
                    'grant':asdict(grant)},indent=2).encode()
                with approval.open('xb') as output:output.write(raw);output.flush();__import__('os').fsync(output.fileno())
                authority.initialize()
                descriptor=Grant(grant.approval_id,grant.binding(),datetime.fromisoformat(grant.expires_at).timestamp(),False)
                authority.import_verified(descriptor,lambda supplied:approval_record==APPROVAL_RECORD
                    and supplied==descriptor and cloud_approval(approval.read_bytes())==grant)
                journal.prepare(freeze_bundle(fresh['bundle'],grant.binding()))
            else:grant=cloud_approval(approval.read_bytes())
            require(grant.job_id==JOB and grant.bundle_hash==BUNDLE and grant.receipt_hash==RECEIPT
                and grant.max_calls==3 and grant.api_bundle_hash==proof['api_bundle_hash'],'existing_approval_scope_changed')
            if disconnect_after_send:
                require(not any(e['attempts'] or e['history'] or e['reconciliations']
                    for e in journal._read()['entries'].values()),'disconnect_test_already_attempted')
            local=ReviewedLocalEvidence(directory,approval,approval_sha256=digest(approval.read_bytes()))
            @contextmanager
            def protected(candidate):
                with verified_remote() as live:
                    require(live['api_bundle_hash']==candidate.api_bundle_hash,'api_changed')
                    with local.snapshot(candidate) as built:yield built
            def current(candidate):
                verify_proof(proof,json.loads(MANIFEST.read_bytes()),json.loads(OWNER_RECEIPT.read_bytes()),now())
                return candidate==grant
            backend=PreparedBackend('https://mantuehuzptactymjpqg.supabase.co')
            transport=DispatchTransport(DisconnectAfterSend(backend) if disconnect_after_send else backend)
            # Keep the ordinary upload hold leased for this bounded process.
            # Source DB locks are still acquired only around final byte handoff.
            with local.files(grant):
                async with httpx.AsyncClient(transport=transport,follow_redirects=False) as client:
                    sender=ReviewedDeepSeekDispatch(journal=journal,authority=authority,transport=transport,client=client,
                        protected_preview=protected,current=current,clock=now)
                    result=await sender.step(grant)
            (cache/'last-result.json').write_text(json.dumps({'at':now().isoformat(),**result},indent=2),encoding='utf8')
            return result
        finally:lock.close()


def check(directory):
    # Local preflight must not initialize Windows asyncio loopback sockets.
    preview(Path(directory).resolve())
    with verified_remote():
        return {'state':'ready_for_authorized_test','records':4,'resources':0,'network_requests':0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=ROOT.parent/'data/attention-desk')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--execute',action='store_true');mode.add_argument('--revoke',action='store_true')
    parser.add_argument('--approval-record')
    parser.add_argument('--disconnect-after-send',action='store_true',help='First publication only: exercise a lost network response')
    args=parser.parse_args()
    try:
        require(not args.disconnect_after_send or args.execute,'disconnect_requires_execute')
        result=(asyncio.run(run(args.directory,execute=args.execute,approval_record=args.approval_record,revoke=args.revoke,
            disconnect_after_send=args.disconnect_after_send))
            if args.execute or args.revoke else check(args.directory))
    except DispatchDenied as error:result={'state':str(error)}
    except Exception:result={'state':'preflight_unavailable'}
    print(json.dumps(result))
    return 0 if result['state'] in {'ready_for_authorized_test','acknowledged','revoked'} else 2


if __name__=='__main__':raise SystemExit(main())
