"""Explicit v2 activation and the existing receipt/fence consumer lifecycle."""
import asyncio
from contextlib import ExitStack,closing
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import httpx

from ..cloud_store import ProcessLock
from ..personal_store import PersonalStore,encode
from ..user_context import UserContext,UserContextStore
from ..wechat_collect.automation import POLICY,FILENAME,activation,instant
from ..wechat_collect.deepseek_content_gate import leased_grant,binding_digest
from .deepseek_cloud_scope import ContinuousCloudGrant,CloudSource
from .deepseek_cloud_continuous import continuous_approval,ContinuousLocalEvidence
from .deepseek_cloud_consumer import ConsumerState,DeepSeekCloudConsumer
from .deepseek_cloud_evidence import EVIDENCE_FILES,evidence_digest
from .deepseek_dispatch import ReviewedDeepSeekDispatch
from .wechat_dispatch_authority import DispatchAuthority,Grant,DispatchDenied
from .wechat_incremental_journal import WeChatIncrementalJournal,JournalError
from .wechat_dispatch_transport import DispatchTransport
from .wechat_prepared_proxy import PreparedLocalProxyBackend
from .settings import WorkerSettings,ENDPOINT

ROOT=Path(__file__).resolve().parents[2]
CACHE='cloud-sync/notification-automation'


def sha(raw):return hashlib.sha256(raw).hexdigest()
def now():return datetime.now(timezone.utc)
def implementation_hash():
    names=['wechat_collect/'+n+'.py' for n in ('analysis','automation','deepseek_worker','deepseek_content_gate',
        'config','models','quality','json_output','deepseek_projection','qr_text_policy','recovery','ingestion','filtering','service','store','health','scheduler')]
    names+=['cloud_sync/'+n+'.py' for n in ('automatic_actions','notification_runtime','deepseek_cloud_scope',
        'deepseek_cloud_continuous','deepseek_cloud_consumer','deepseek_cloud_evidence','deepseek_dispatch',
        'wechat_incremental_journal','wechat_dispatch_authority','wechat_dispatch_transport','wechat_prepared_connection',
        'wechat_prepared_proxy','wechat_file_lease','wechat_outbound_validation','minimization','minimized_projection','settings','deepseek_cloud_launch')]
    names+=['review_store.py','personal_store.py','personal_priority.py','personal_models.py','extraction_models.py','user_context.py','source_metadata.py',
            'notification_supervisor.py','notification_health.py','desktop_entrypoint.py','ai_transport.py','recovery_controls.py',
            '../scripts/notification-automation.py']
    return sha(encode({p:sha((ROOT/'server'/p).read_bytes()) for p in names}).encode())


def prepare(directory,spec,api,*,at=None):
    """Read-only source checkpoint; caller writes the plan outside production."""
    directory=Path(directory).resolve(); at=instant(at or now())
    if not spec.get('authorization_record') or instant(spec['approved_at'])>at:
        raise ValueError('explicit_automation_approval_required')
    current=UserContextStore(directory).read()
    values={**current.model_dump(),**spec['profile']}
    values['role_assertions']={**{k:v.model_dump() for k,v in current.role_assertions.items()},**spec['profile'].get('role_assertions',{})}
    values['confirmed_fields']=list(dict.fromkeys([*current.confirmed_fields,*spec['profile'].get('confirmed_fields',[])]))
    values['courses']=list(dict.fromkeys([*current.courses,*spec['profile'].get('courses',[])]))
    profile=UserContext.model_validate(values)
    personal=PersonalStore(directory/'personal.sqlite3')
    controls=json.loads((directory/'recovery-controls.json').read_bytes())
    if (controls.get('version')!=1 or
            any(type(controls.get(k)) is not bool for k in ('pause_paid_ai','hold_cloud_uploads'))):
        raise ValueError('global_controls_invalid')
    settings=WorkerSettings(directory/'cloud-worker-settings.json').read()
    if not settings or settings.get('endpoint')!=ENDPOINT or settings.get('enabled') is not True:
        raise ValueError('existing_cloud_connection_required')
    if len(spec['audiences'])!=5:raise ValueError('five_explicit_audiences_required')
    sources={};cloud_sources=[]
    with leased_grant(personal) as deepseek:
        with closing(sqlite3.connect(personal.path.as_uri()+'?mode=ro',uri=True)) as c:
            c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
            for chat_id,audience in spec['audiences'].items():
                row=c.execute('SELECT * FROM wechat_chat WHERE id=?',(chat_id,)).fetchone()
                if not row or not row['enabled']:raise ValueError('source_not_enabled')
                binding=binding_digest(row['source_id'],row['binding_json'])
                if deepseek.source_bindings.get(chat_id)!=binding:raise ValueError('source_binding_changed')
                seq=c.execute('SELECT coalesce(max(ingest_seq),0) FROM wechat_message WHERE chat_id=?',(chat_id,)).fetchone()[0]
                sources[chat_id]={'after_seq':seq,'binding_hash':binding,'audience':audience}
                cloud_sources.append(CloudSource(chat_id,row['source_id'],binding,seq))
    value={'version':2,'policy':POLICY,'enabled':True,'authorization_record':spec['authorization_record'],
        'approved_at':spec['approved_at'],'not_before':at.isoformat(),'sources':sources,'profile_hash':profile.fingerprint(),
        'permissions':{'analyze':True,'create_tasks':True,'publish_tasks':True},
        'http_daily':spec.get('http_daily',1000),'tokens_daily':spec.get('tokens_daily')}
    cfg={k:settings[k] for k in ('workspace_id','processor_id')}
    server_scope={'enabled':True,**cfg,'not_before':at.isoformat(),'activation_hash':sha(encode(value).encode()),
                  'source_ids':[s.source_id for s in cloud_sources]}
    files={n:sha((directory/n).read_bytes()) for n in EVIDENCE_FILES}
    return {'version':2,'activation':value,'profile':profile.model_dump(),
        'previous_profile_hash':current.fingerprint(),'evidence_files':files,'api_scope':server_scope,
        'api_manifest':api,'implementation_hash':implementation_hash(),'cloud_sources':[asdict(s) for s in cloud_sources]}


def activate(directory,plan,proof,*,at=None):
    """Called only after deployment approval and fresh remote scope/file verification."""
    directory=Path(directory).resolve(); at=instant(at or now()); value=plan['activation']; cache=directory/CACHE
    controls=json.loads((directory/'recovery-controls.json').read_bytes())
    if (controls.get('version')!=1 or controls.get('pause_paid_ai') is not False
            or controls.get('hold_cloud_uploads') is not False):
        raise ValueError('global_automation_paused')
    if (not 0<=(at-instant(value['not_before'])).total_seconds()<=900
            or sha(encode(value).encode())!=plan['api_scope']['activation_hash']
            or value['profile_hash']!=UserContext.model_validate(plan['profile']).fingerprint()
            or not 0<=(at-instant(proof['checked_at'])).total_seconds()<=900
            or plan['implementation_hash']!=implementation_hash()
            or proof['files']!=plan['api_manifest']['files'] or proof['api_version']<=plan['api_manifest']['baseline_version']
            or proof['api_scope']!=plan['api_scope'] or proof['processor_revoked'] is not False
            or proof['workspace_id']!=plan['api_scope']['workspace_id']
            or proof['processor_id']!=plan['api_scope']['processor_id']):
        raise ValueError('fresh_matching_deployment_proof_required')
    if UserContextStore(directory).read().fingerprint()!=plan['previous_profile_hash']:
        raise ValueError('profile_changed_since_review')
    for name,expected in plan['evidence_files'].items():
        if sha((directory/name).read_bytes())!=expected:raise ValueError('activation_evidence_changed')
    if cache.exists() or (directory/FILENAME).exists():raise ValueError('existing_automation_must_not_be_overwritten')
    from uuid import uuid4
    files={n:(directory/n).read_bytes() for n in EVIDENCE_FILES};files[FILENAME]=encode(value).encode()
    scope=ContinuousCloudGrant(str(uuid4()),value['approved_at'],value['not_before'],
        proof['workspace_id'],proof['processor_id'],proof['publisher_epoch'],proof['generation'],
        evidence_digest(files),proof['api_bundle_hash'],tuple(CloudSource(**s) for s in plan['cloud_sources']),
        daily_limit=value['http_daily'],policy=POLICY)
    approval={'version':2,'mode':'continuous','enabled':True,'authorization_record':value['authorization_record'],'scope':asdict(scope)}
    raw=encode(approval).encode();cache.mkdir(parents=True)
    authority=DispatchAuthority(cache/'authority.sqlite3');authority.initialize()
    imported=Grant(scope.approval_id,scope.binding(),instant(scope.expires_at).timestamp(),False)
    authority.import_verified(imported,lambda candidate:candidate==imported and continuous_approval(raw)==scope)
    ConsumerState(cache/'runtime.sqlite3',scope).initialize()
    for name,data in {'approval.json':raw,'journal.json':encode({'version':4,'entries':{}}).encode(),
        'release.json':encode({'version':2,'implementation_hash':implementation_hash(),'approval_sha256':sha(raw),'proof':proof}).encode()}.items():
        with (cache/name).open('xb') as f:f.write(data)
    # The enable file is last. A partial activation cannot authorize any work.
    store=UserContextStore(directory);store.save(UserContext.model_validate(plan['profile']),store.read().profile_version)
    with (directory/FILENAME).open('xb') as f:f.write(encode(value).encode())
    return {'state':'activated_not_started','not_before':scope.not_before,'history_backfill':False}


def load(directory):
    directory=Path(directory).resolve();cache=directory/CACHE
    raw=(cache/'approval.json').read_bytes();scope=continuous_approval(raw)
    release=json.loads((cache/'release.json').read_bytes())
    if (scope.policy!=POLICY or release['implementation_hash']!=implementation_hash()
            or release['approval_sha256']!=sha(raw) or release['proof']['api_bundle_hash']!=scope.api_bundle_hash):
        raise DispatchDenied('automation_release_changed')
    local=ContinuousLocalEvidence(directory,cache/'approval.json',approval_sha256=sha(raw))
    with local.files(scope):pass
    return cache,scope,local


async def run(directory,*,once=False,interval=60):
    directory=Path(directory).resolve(); cache,scope,local=load(directory)
    lock=ProcessLock(cache);lock.acquire()
    try:
        journal=WeChatIncrementalJournal(cache/'journal.json',lock);authority=DispatchAuthority(cache/'authority.sqlite3')
        backend=PreparedLocalProxyBackend(ENDPOINT.split('/functions/')[0],'http://127.0.0.1:7897')
        transport=DispatchTransport(backend)
        async with httpx.AsyncClient(transport=transport,follow_redirects=False,timeout=15) as client, \
                httpx.AsyncClient(proxy='http://127.0.0.1:7897',trust_env=False,follow_redirects=False,timeout=10) as metadata:
            async def status_read(config):
                r=await metadata.get(ENDPOINT+'/worker/wechat-actions-status',headers={'X-Eric-Worker-Key':config['worker_key']})
                if r.status_code!=200 or len(r.content)>4096:raise DispatchDenied('publisher_status_unavailable')
                return r.json()
            def current_release():
                try:load(directory);return True
                except (OSError,ValueError,KeyError,DispatchDenied):return False
            consumer=DeepSeekCloudConsumer(scope=scope,state=ConsumerState(cache/'runtime.sqlite3',scope),local=local,
                authority=authority,journal=journal,status_read=status_read,
                sender_factory=lambda current:ReviewedDeepSeekDispatch(journal=journal,authority=authority,transport=transport,
                    client=client,protected_preview=local.snapshot,current=current,clock=now),clock=now,release_current=current_release)
            try:previous=json.loads((cache/'status.json').read_text('utf-8'))
            except (OSError,ValueError):previous={}
            while True:
                try:result=await consumer.tick()
                except (DispatchDenied,JournalError) as error:result={'state':str(error) if str(error).replace('_','').isalnum() else 'sync_guard_failed'}
                except httpx.TransportError:result={'state':'network_unavailable'}
                except Exception:result={'state':'automatic_sync_error'}
                result=progress_health(result,previous);previous=result
                import os
                from ..notification_supervisor import process_token
                result.update(at=now().isoformat(),pid=os.getpid(),process_token=process_token(),service=POLICY,full_cloud_sync_enabled=False)
                temp=cache/'status.tmp';temp.write_text(encode(result),'utf-8');temp.replace(cache/'status.json')
                if once:return result
                await asyncio.sleep(interval)
    finally:lock.close()


def progress_health(result,previous):
    benign={'acknowledged','fenced_absent','backoff','status_backoff','daily_limit_reached','cycle_limit_reached'}
    failed=result.get('state') in {'network_unavailable','publisher_status_unavailable','automatic_sync_error',
        'protected_preview_unavailable','sync_guard_failed'} or any(s not in benign for s in result.get('results',[]))
    failures=previous.get('consecutive_failures',0)+1 if failed else 0
    return {**result,'consecutive_failures':failures,'alert_required':failures>=3 or
        result.get('state') in {'automation_release_changed','grant_not_current'} or
        'delivery_attempt_limit_reached' in result.get('results',[])}
