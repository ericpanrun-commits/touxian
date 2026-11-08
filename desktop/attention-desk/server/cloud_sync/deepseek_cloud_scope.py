"""Explicit future-content scope for DeepSeek-to-Inbox synchronization.

This is not the reviewed one-notice approval. It cannot activate itself, inherit
DeepSeek's destination permission, or authorize a historical backlog.
"""
from dataclasses import dataclass,asdict
from datetime import datetime,timezone
import hashlib
import json
import re

from .settings import ENDPOINT
from ..wechat_collect.deepseek_content_gate import binding_digest

SHA=re.compile(r'^[0-9a-f]{64}$')
UUID=re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
POLICY='deepseek-new-cloud-notices-v1'
UNTIL_REVOKED='9999-12-30T00:00:00+00:00'


def instant(value):
    value=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('reliable_timestamp_required')
    return value.astimezone(timezone.utc)


def fingerprint(value):
    if not isinstance(value,str) or not SHA.fullmatch(value):raise ValueError('invalid_fingerprint')


@dataclass(frozen=True)
class CloudSource:
    chat_id: str
    source_id: str
    binding_hash: str
    after_seq: int

    def __post_init__(self):
        if (not isinstance(self.chat_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',self.chat_id)
                or self.source_id!='wechat:'+self.chat_id or type(self.after_seq) is not int or self.after_seq<0):
            raise ValueError('invalid_source_scope')
        fingerprint(self.binding_hash)


@dataclass(frozen=True)
class ContinuousCloudGrant:
    approval_id: str
    approved_at: str
    not_before: str
    workspace_id: str
    processor_id: str
    epoch: str
    generation: int
    evidence_hash: str
    api_bundle_hash: str
    sources: tuple[CloudSource,...]
    daily_limit: int = 30
    cycle_limit: int = 4
    expires_at: str = UNTIL_REVOKED
    destination: str = ENDPOINT
    policy: str = POLICY

    def __post_init__(self):
        for name in ('approval_id','workspace_id','processor_id','epoch'):
            value=getattr(self,name)
            if not isinstance(value,str) or not UUID.fullmatch(value):raise ValueError('invalid_identity')
        for name in ('evidence_hash','api_bundle_hash'):fingerprint(getattr(self,name))
        if (self.destination!=ENDPOINT or self.policy!=POLICY or type(self.sources) is not tuple
                or len(self.sources)!=5 or any(type(source) is not CloudSource for source in self.sources)
                or len({source.chat_id for source in self.sources})!=5):
            raise ValueError('five_explicit_sources_required')
        if not instant(self.approved_at)<=instant(self.not_before)<instant(self.expires_at):
            raise ValueError('invalid_future_window')
        if (type(self.generation) is not int or not 0<=self.generation<9007199254740990
                or type(self.daily_limit) is not int or not 1<=self.daily_limit<=30
                or type(self.cycle_limit) is not int or not 1<=self.cycle_limit<=min(4,self.daily_limit)):
            raise ValueError('invalid_limits')

    def binding(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True,separators=(',',':')).encode()).hexdigest()


@dataclass(frozen=True)
class ScopedCloudPublication:
    """A fresh model-backed projection under an independent continuous grant.

    This is never permission by itself. The protected builder rechecks every
    selected message against scope, and DispatchAuthority owns the parent grant.
    """
    scope: ContinuousCloudGrant
    job_id: str
    receipt_hash: str
    bundle_hash: str

    def __post_init__(self):
        if type(self.scope) is not ContinuousCloudGrant or not isinstance(self.job_id,str) or not UUID.fullmatch(self.job_id):
            raise ValueError('continuous_scope_required')
        fingerprint(self.receipt_hash);fingerprint(self.bundle_hash)

    def binding(self):return self.scope.binding()
    @property
    def approval_id(self):return self.scope.approval_id
    @property
    def approved_at(self):return self.scope.not_before
    @property
    def expires_at(self):return self.scope.expires_at
    @property
    def workspace_id(self):return self.scope.workspace_id
    @property
    def processor_id(self):return self.scope.processor_id
    @property
    def epoch(self):return self.scope.epoch
    @property
    def generation(self):return self.scope.generation
    @property
    def max_calls(self):return None  # Durable global quotas and backoff apply.
    @property
    def daily_limit(self):return self.scope.daily_limit
    @property
    def cycle_limit(self):return self.scope.cycle_limit
    @property
    def evidence_hash(self):return self.scope.evidence_hash
    @property
    def api_bundle_hash(self):return self.scope.api_bundle_hash


def validate_future_scope(connection,scope,preview):
    """Call inside the same locked source transaction that built the preview."""
    if not connection.in_transaction or type(scope) is not ContinuousCloudGrant:
        raise ValueError('protected_future_scope_required')
    records={record['kind']:record['payload'] for record in preview['bundle']['records']}
    raw=records['raw_item'];snapshot=records['raw_review']['snapshot']
    allowed=next((source for source in scope.sources if source.source_id==raw['source_id']),None)
    if not allowed:raise ValueError('source_outside_cloud_scope')
    cutoff=instant(scope.not_before)
    if instant(raw['created_at'])<cutoff or instant(snapshot['created_at'])<cutoff:
        raise ValueError('historical_cloud_content_not_authorized')
    messages=snapshot['wechat']['messages']
    if not messages:raise ValueError('text_evidence_required')
    for evidence in messages:
        row=connection.execute('''SELECT m.*,ch.enabled,ch.source_id AS actual_source,ch.binding_json
            FROM wechat_message m JOIN wechat_chat ch ON ch.id=m.chat_id WHERE m.id=?''',(evidence['message_id'],)).fetchone()
        if (not row or not row['enabled'] or row['chat_id']!=allowed.chat_id or row['actual_source']!=allowed.source_id
                or binding_digest(row['actual_source'],row['binding_json'])!=allowed.binding_hash
                or row['ingest_seq']<=allowed.after_seq or row['message_type']!='text' or row['time_reliability']!='reliable'):
            raise ValueError('source_outside_cloud_scope')
        if instant(row['source_time'])<cutoff or instant(row['captured_at'])<cutoff:
            raise ValueError('historical_cloud_content_not_authorized')
    return True
