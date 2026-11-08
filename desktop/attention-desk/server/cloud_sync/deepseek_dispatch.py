"""Bounded reviewed-notice adapter for the existing protected transport.

No scheduler, credential lookup, approval import or production enablement lives
here. The owning process supplies its journal lock, independently verified cloud
grant, current session proof and a source/file-protected preview context. A
DeepSeek content grant alone can never construct a usable cloud authorization.
"""
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3

import httpx

from .settings import ENDPOINT
from .wechat_dispatch_authority import DispatchDenied
from .wechat_dispatch_transport import DispatchTransport
from .wechat_incremental_journal import (
    JournalError, freeze_bundle, reconciliation_proof, reconciliation_request,
)

SHA=re.compile(r'^[a-f0-9]{64}$')
UUID=re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')


def _instant(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    if not isinstance(result,datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('timezone_required')
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReviewedCloudGrant:
    """Exactly one independently approved analysis-backed cloud projection."""
    approval_id: str
    job_id: str
    receipt_hash: str
    bundle_hash: str
    workspace_id: str
    processor_id: str
    epoch: str
    generation: int
    approved_at: str
    expires_at: str
    evidence_hash: str
    api_bundle_hash: str
    max_calls: int = 3

    def __post_init__(self):
        for key in ('approval_id','job_id','workspace_id','processor_id','epoch'):
            if not isinstance(getattr(self,key),str) or not UUID.fullmatch(getattr(self,key)):
                raise ValueError('invalid_identity')
        for key in ('receipt_hash','bundle_hash','evidence_hash','api_bundle_hash'):
            if not isinstance(getattr(self,key),str) or not SHA.fullmatch(getattr(self,key)):
                raise ValueError('invalid_fingerprint')
        if not isinstance(self.approved_at,str) or not isinstance(self.expires_at,str):
            raise ValueError('timestamp_string_required')
        lifetime=(_instant(self.expires_at)-_instant(self.approved_at)).total_seconds()
        if not 0<lifetime<=86400 or type(self.max_calls) is not int or not 1<=self.max_calls<=5:
            raise ValueError('invalid_test_limits')
        if type(self.generation) is not int or not 0<=self.generation<9007199254740990:
            raise ValueError('invalid_generation')

    def binding(self):
        return hashlib.sha256(json.dumps({'mode':'reviewed-deepseek-cloud-v1',
            'endpoint':ENDPOINT,**asdict(self)},sort_keys=True,separators=(',',':')).encode()).hexdigest()


class ReviewedDeepSeekDispatch:
    """Single-step operation; unknown delivery is reconciled before any resend.

    protected_preview(grant) must lease *all* approval/configuration evidence and
    use locked_source() for the final preview build. It yields (preview, config).
    current(grant) verifies approval, implementation, scope and API/session proof;
    the durable DispatchAuthority, not that callback, orders actual revocation.
    No ordinary HTTP client or unprotected preview fallback is supported.
    """
    def __init__(self, *, journal, authority, transport, client, protected_preview, current, clock):
        if type(transport) is not DispatchTransport or client._transport is not transport:
            raise ValueError('protected_transport_required')
        self.journal,self.authority,self.transport,self.client=journal,authority,transport,client
        self.protected_preview,self.current,self.clock=protected_preview,current,clock

    def _current(self, grant):
        self.journal._require_owner()
        if not _instant(grant.approved_at)<=_instant(self.clock())<_instant(grant.expires_at):
            raise DispatchDenied('cloud_authorization_inactive')
        try: current=self.current(grant)
        except Exception: current=False
        if current is not True:
            raise DispatchDenied('cloud_scope_or_session_changed')

    def _checked(self, grant, preview, config):
        if (preview.get('job_id')!=grant.job_id or preview.get('analysis_receipt_sha256')!=grant.receipt_hash
                or preview.get('upload_authorized') is not False or preview.get('cloud_outbound_validation')!='passed'
                or preview['bundle'].get('bundle_hash')!=grant.bundle_hash):
            raise DispatchDenied('reviewed_projection_changed')
        if (not isinstance(config,dict) or config.get('endpoint')!=ENDPOINT or config.get('enabled') is not True
                or config.get('workspace_id')!=grant.workspace_id or config.get('processor_id')!=grant.processor_id
                or not isinstance(config.get('worker_key'),str) or not re.fullmatch(r'ework_[A-Za-z0-9_-]{43}',config['worker_key'])):
            raise DispatchDenied('cloud_connection_changed')
        return freeze_bundle(preview['bundle'],grant.binding())

    async def step(self, grant):
        if type(grant) is not ReviewedCloudGrant:
            return {'state':'cloud_authorization_required'}
        try:
            self._current(grant)
            # Initial construction is protected too; connection preparation for
            # the final transmission happens later, outside every source lease.
            with self.protected_preview(grant) as (preview,config):
                meta=self._checked(grant,preview,config)
                config=dict(config)
                entry=self.journal.prepare(meta)
                records=preview['bundle']['records']
            if entry['state'] in {'acknowledged','blocked','superseded'}:
                return {'state':entry['state']}
            history=entry['history']
            count=len(entry['attempts'])+len(entry['reconciliations'])+sum(
                len(h['attempts'])+len(h['reconciliations']) for h in history)
            if count>=grant.max_calls:
                return {'state':'test_call_limit_reached'}
            stamps=[a['at'] for a in entry['attempts']]+entry['reconciliations']
            stamps += [a['at'] for h in history for a in h['attempts']]
            stamps += [t for h in history for t in h['reconciliations']]
            if stamps and (_instant(self.clock())-max(_instant(t) for t in stamps)).total_seconds()<30:
                return {'state':'backoff'}
            generation=grant.generation
            if entry['state']=='fenced':
                generation=entry['fence']['generation']
                self.journal.rearm_fenced(meta['dedupe_key'],meta)
                entry=self.journal.get(meta['dedupe_key'])
            if entry['history']:
                generation=max(generation,entry['history'][-1]['fence']['generation'])
            reconcile=entry['state']=='unconfirmed'
            if reconcile:
                entry=self.journal.claim_reconciliation(meta['dedupe_key'],at=self.clock(),
                    daily_limit=grant.max_calls,cycle_limit=grant.max_calls)
                body=reconciliation_request(entry)
                path='/worker/wechat-reconcile'
            else:
                self.journal.claim(meta['dedupe_key'],at=self.clock(),daily_limit=grant.max_calls,
                    cycle_limit=grant.max_calls,epoch=grant.epoch,generation=generation)
                body={key:meta[key] for key in ('raw_id','snapshot_id','source_id')}
                body.update(records=records,epoch=grant.epoch,generation=generation)
                path='/worker/wechat-publish'
            @contextmanager
            def snapshot():
                with self.protected_preview(grant) as (fresh,new_config):
                    if self._checked(grant,fresh,new_config)!=meta or new_config!=config:
                        raise DispatchDenied('reviewed_projection_changed')
                    self._current(grant)
                    expected=body if reconcile else {**body,'records':fresh['bundle']['records']}
                    yield httpx.Request('POST',ENDPOINT+path,json=expected).content
            def validate():
                self._current(grant)
                return True
            response=await self.transport.post(self.client,ENDPOINT+path,
                authority=self.authority,identity=grant.approval_id,binding=grant.binding(),
                snapshot=snapshot,validate=validate,headers={'X-Eric-Worker-Key':config['worker_key']},
                json=body,follow_redirects=False)
            try: value=response.json()
            except ValueError: value=None
            if response.status_code==200 and isinstance(value,dict):
                if reconcile:
                    if (set(value)!={'state','proof','generation'} or value['proof']!=reconciliation_proof(entry)
                            or type(value['generation']) is not int):
                        return {'state':'needs_reconciliation'}
                    if value['state']=='fenced_absent':
                        self.journal.record_fence(meta['dedupe_key'],receipt=value,at=self.clock())
                        return {'state':'fenced_absent'}
                    if value['state']!='already_published' or value['generation']!=entry['attempts'][0]['generation']:
                        return {'state':'needs_reconciliation'}
                if value.get('state') in {'published','already_published'}:
                    self.journal.acknowledge(meta['dedupe_key'],at=self.clock(),state=value['state'],revision=value.get('revision'))
                    return {'state':'acknowledged'}
            # Unknown/malformed responses retain the durable claim. Even a 400
            # is not permission to send again; reconciling remains bounded.
            if response.status_code in {401,403}:
                self.journal.block(meta['dedupe_key'],'credential_revoked')
                return {'state':'credential_revoked'}
            return {'state':'needs_reconciliation'}
        except DispatchDenied as error:
            return {'state':str(error)}
        except httpx.TransportError:
            return {'state':'needs_reconciliation'}
        except JournalError as error:
            return {'state':str(error)}
        except (ValueError,KeyError,TypeError,OSError,sqlite3.Error):
            # A changed source/file is not evidence of non-delivery. Preserve
            # any durable unknown claim, and never log raw exception contents.
            return {'state':'protected_preview_unavailable'}
