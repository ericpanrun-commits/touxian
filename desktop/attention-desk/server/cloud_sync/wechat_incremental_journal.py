"""Durable hash-only retry metadata for the existing CloudSync process.

This candidate does not schedule or send anything. The owning CloudSync must
hold its existing ProcessLock. No message text, credentials or payloads are
written to this operational journal.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..personal_store import encode
from .projection import record

SHA = re.compile(r'^[a-f0-9]{64}$')
IDENTITY = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
SOURCE = re.compile(r'^wechat:[A-Za-z0-9_-]{1,80}$')
STATES = {'prepared','unconfirmed','acknowledged','blocked','fenced','superseded'}
META = {'dedupe_key','scope_id','source_id','raw_id','snapshot_id','message_ids','bundle_hash','records','raw_guard','review_guard'}
_LOCK_REGISTRY = {}
_LOCK_REGISTRY_GUARD = threading.Lock()


class JournalError(Exception):
    pass


class SourceSnapshotUnavailable(JournalError):
    """A transient or changed snapshot is not proof that a bundle is invalid."""


def _instant(value):
    if isinstance(value,str):
        value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise JournalError('timezone_required')
    return value.astimezone(timezone.utc)


def freeze_bundle(bundle, scope_id):
    """Reduce a fresh four-view projection to identifiers and fingerprints."""
    from ..wechat_collect.automation import POLICY as ACTION_POLICY
    if isinstance(bundle,dict) and bundle.get('policy')==ACTION_POLICY:
        from .automatic_actions import freeze_actions
        try: return freeze_actions(bundle,scope_id)
        except (ValueError,KeyError,TypeError): raise JournalError('invalid_automatic_projection') from None
    if (not isinstance(bundle,dict) or bundle.get('preview_only') is not True
        or bundle.get('upload_authorized') is not False or bundle.get('resources')!=[]
        or bundle.get('record_count')!=4 or not isinstance(scope_id,str) or not SHA.fullmatch(scope_id)):
        raise JournalError('invalid_preview_or_scope')
    records=bundle.get('records')
    if not isinstance(records,list) or len(records)!=4:
        raise JournalError('invalid_projection_count')
    bykind={}
    for item in records:
        if not isinstance(item,dict) or set(item)!={'kind','id','payload','source_hash'} or item['kind'] in bykind:
            raise JournalError('invalid_projection_shape')
        if item['kind'] not in {'raw_item','inbox_item','inbox','raw_review'}:
            raise JournalError('invalid_projection_kind')
        if record(item['kind'],item['id'],item['payload'])!=item:
            raise JournalError('invalid_projection_hash')
        bykind[item['kind']]=item
    if set(bykind)!={'raw_item','inbox_item','inbox','raw_review'}:
        raise JournalError('invalid_projection_kind')
    raw=bykind['raw_item']; review=bykind['raw_review']; raw_id=raw['id']; snapshot_id=review['id']
    summary=bykind['inbox_item']['payload'];detail=bykind['inbox']['payload']
    try:
        if (raw['payload']['inbox_course_id'] is not None or summary['course'] is not None or
            detail['course'] is not None or review['payload']['item']['inbox_course_id'] is not None or
            detail['review']['item']['inbox_course_id'] is not None):
            raise JournalError('course_outside_notice_scope')
    except (KeyError,TypeError):
        raise JournalError('invalid_projection_shape') from None
    source_id=raw['payload'].get('source_id','')
    if (not isinstance(raw_id,str) or not IDENTITY.fullmatch(raw_id) or
        not isinstance(snapshot_id,str) or not IDENTITY.fullmatch(snapshot_id) or
        not isinstance(source_id,str) or not SOURCE.fullmatch(source_id) or
        bykind['inbox_item']['id']!=raw_id or bykind['inbox']['id']!=raw_id or
        raw['payload'].get('id')!=raw_id or raw['payload'].get('inbox_snapshot_id')!=snapshot_id or
        review['payload'].get('raw_snapshot_id')!=snapshot_id or bundle.get('raw_item_ids')!=[raw_id]):
        raise JournalError('projection_identity_mismatch')
    guards=bundle.get('local_guards')
    if (not isinstance(guards,list) or len(guards)!=1 or not isinstance(guards[0],dict) or
        set(guards[0])!={'raw_id','snapshot_id','raw_guard','review_guard'} or
        guards[0]['raw_id']!=raw_id or guards[0]['snapshot_id']!=snapshot_id or
        any(not isinstance(guards[0][key],str) or not SHA.fullmatch(guards[0][key]) for key in ('raw_guard','review_guard')) or
        review['payload'].get('status') not in {'pending','archived'} or
        summary.get('review_status')!=review['payload'].get('status')):
        raise JournalError('missing_local_guards')
    snapshot=review['payload'].get('snapshot') or {}
    messages=(snapshot.get('wechat') or {}).get('messages')
    if not isinstance(messages,list) or not messages:
        raise JournalError('missing_source_evidence')
    message_ids=[message.get('message_id') for message in messages if isinstance(message,dict)]
    if (len(message_ids)!=len(messages) or any(not isinstance(mid,str) or not IDENTITY.fullmatch(mid) for mid in message_ids)
        or len(message_ids)!=len(set(message_ids))):
        raise JournalError('invalid_source_evidence')
    # Reject hidden fields, extra metadata and detectable unredacted text on
    # the host. A cloud-side rejection would occur after a payload was sent.
    from .wechat_outbound_validation import validate_outbound
    try:
        validate_outbound(bykind,raw_id,snapshot_id,source_id)
    except (KeyError,TypeError,ValueError,UnicodeError,AttributeError):
        raise JournalError('invalid_outbound_projection') from None
    bundle_hash=hashlib.sha256(encode(records).encode()).hexdigest()
    if bundle.get('bundle_hash')!=bundle_hash:
        raise JournalError('bundle_changed')
    dedupe=hashlib.sha256(encode([source_id,raw_id,sorted(message_ids)]).encode()).hexdigest()
    descriptors=sorted(({'kind':r['kind'],'id':r['id'],'source_hash':r['source_hash']} for r in records),key=lambda r:r['kind'])
    return {'dedupe_key':dedupe,'scope_id':scope_id,'source_id':source_id,'raw_id':raw_id,
        'snapshot_id':snapshot_id,'message_ids':sorted(message_ids),'bundle_hash':bundle_hash,'records':descriptors,
        'raw_guard':guards[0]['raw_guard'],'review_guard':guards[0]['review_guard']}


def _valid_metadata(value):
    if not isinstance(value,dict) or set(value)!=META:
        return False
    if any(not isinstance(value[k],str) or not SHA.fullmatch(value[k]) for k in ('dedupe_key','scope_id','bundle_hash','raw_guard','review_guard')):
        return False
    if any(not isinstance(value[k],str) or not IDENTITY.fullmatch(value[k]) for k in ('raw_id','snapshot_id')):
        return False
    if not isinstance(value['source_id'],str) or not SOURCE.fullmatch(value['source_id']):return False
    mids=value['message_ids']
    if (not isinstance(mids,list) or not mids or len(mids)>100 or
        any(not isinstance(mid,str) or not IDENTITY.fullmatch(mid) for mid in mids)):
        return False
    if mids!=sorted(set(mids)):return False
    expected=hashlib.sha256(encode([value['source_id'],value['raw_id'],mids]).encode()).hexdigest()
    if value['dedupe_key']!=expected:return False
    rows=value['records']
    if isinstance(rows,list) and len(rows)>4:
        from .automatic_actions import metadata_valid
        try: return metadata_valid(value)
        except (ValueError,KeyError,TypeError): return False
    if not isinstance(rows,list) or len(rows)!=4 or any(not isinstance(r,dict) for r in rows):return False
    kinds={'raw_item':value['raw_id'],'inbox_item':value['raw_id'],'inbox':value['raw_id'],'raw_review':value['snapshot_id']}
    if [r.get('kind') for r in rows]!=sorted(kinds):return False
    return all(set(row)=={'kind','id','source_hash'} and row['id']==kinds[row['kind']] and
        isinstance(row['source_hash'],str) and SHA.fullmatch(row['source_hash']) for row in rows)


def reconciliation_request(entry):
    """Identifiers and hashes only; the original notice text never leaves here."""
    if not entry or not entry.get('attempts'):raise JournalError('missing_attempt')
    attempt=entry['attempts'][0]
    return {'raw_id':entry['raw_id'],'snapshot_id':entry['snapshot_id'],'source_id':entry['source_id'],
        'records':entry['records'],'epoch':attempt['epoch'],'generation':attempt['generation']}


def reconciliation_proof(entry):
    value=json.dumps(reconciliation_request(entry),ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(value.encode()).hexdigest()


class WeChatIncrementalJournal:
    """One file under the original CloudSync cache, guarded by its process lock."""
    def __init__(self, path, process_lock):
        self.path=Path(path)
        self.process_lock=process_lock
        with _LOCK_REGISTRY_GUARD:
            self.lock=_LOCK_REGISTRY.setdefault(str(self.path.resolve()),threading.RLock())

    def _require_owner(self):
        if (not getattr(self.process_lock,'file',None) or
            getattr(self.process_lock.file,'closed',True) is not False or
            Path(self.process_lock.path).parent.resolve()!=self.path.parent.resolve()):
            raise JournalError('existing_sync_lock_required')

    def _read(self):
        self._require_owner()
        if not self.path.exists():return {'version':4,'entries':{}}
        try:value=json.loads(self.path.read_text(encoding='utf8'))
        except (OSError,ValueError,UnicodeError):raise JournalError('journal_unreadable') from None
        if (not isinstance(value,dict) or set(value)!={'version','entries'} or type(value['version']) is not int or value['version'] not in {3,4}
            or not isinstance(value['entries'],dict)):
            raise JournalError('journal_invalid')
        if value['version']==3:
            # Upgrade only in memory; the old file remains intact until the
            # next normal atomic write. Reject unexpected extra fields.
            old_keys=META|{'state','attempts','reconciliations','ack_at','revision','blocked_reason','fence','predecessor','successor'}
            for entry in value['entries'].values():
                if not isinstance(entry,dict) or set(entry)!=old_keys:raise JournalError('journal_invalid')
                entry['history']=[]
            value['version']=4
        for key,entry in value['entries'].items():
            if (not isinstance(entry,dict) or set(entry)!=META|{'state','attempts','reconciliations','ack_at','revision','blocked_reason','fence','predecessor','successor','history'}
                or entry.get('dedupe_key')!=key or entry.get('state') not in STATES
                or not _valid_metadata({field:entry[field] for field in META}) or not isinstance(entry['attempts'],list)
                or len(entry['attempts'])>10000 or not isinstance(entry['reconciliations'],list) or len(entry['reconciliations'])>10000
                or not isinstance(entry['history'],list) or len(entry['history'])>1000):
                raise JournalError('journal_invalid')
            try:
                previous_end=None;minimum_generation=0
                for completed in entry['history']:
                    if (not isinstance(completed,dict) or set(completed)!={'attempts','reconciliations','fence'}
                        or not isinstance(completed['attempts'],list) or not 1<=len(completed['attempts'])<=10000
                        or not isinstance(completed['reconciliations'],list) or not 1<=len(completed['reconciliations'])<=10000):
                        raise JournalError('journal_invalid')
                    attempts=completed['attempts'];reconciliations=completed['reconciliations'];fence=completed['fence']
                    for attempt in attempts:
                        if (not isinstance(attempt,dict) or set(attempt)!={'at','epoch','generation'} or
                            not isinstance(attempt['epoch'],str) or not IDENTITY.fullmatch(attempt['epoch']) or
                            type(attempt['generation']) is not int or not minimum_generation<=attempt['generation']<9007199254740991):
                            raise JournalError('journal_invalid')
                        _instant(attempt['at'])
                    if (len({(item['epoch'],item['generation']) for item in attempts})!=1 or
                        any(_instant(left['at'])>_instant(right['at']) for left,right in zip(attempts,attempts[1:])) or
                        previous_end is not None and _instant(attempts[0]['at'])<previous_end):
                        raise JournalError('journal_invalid')
                    stamps=[_instant(stamp) for stamp in reconciliations]
                    if stamps[0]<_instant(attempts[-1]['at']) or any(a>b for a,b in zip(stamps,stamps[1:])):
                        raise JournalError('journal_invalid')
                    prior={**entry,'attempts':attempts}
                    if (not isinstance(fence,dict) or set(fence)!={'proof','generation','at'} or
                        fence['proof']!=reconciliation_proof(prior) or type(fence['generation']) is not int or
                        not attempts[0]['generation']<fence['generation']<=9007199254740991 or _instant(fence['at'])<stamps[-1]):
                        raise JournalError('journal_invalid')
                    previous_end=_instant(fence['at']);minimum_generation=fence['generation']
                for attempt in entry['attempts']:
                    if (not isinstance(attempt,dict) or set(attempt)!={'at','epoch','generation'} or
                        not isinstance(attempt['epoch'],str) or not IDENTITY.fullmatch(attempt['epoch']) or
                        type(attempt['generation']) is not int or not 0<=attempt['generation']<9007199254740991):
                        raise JournalError('journal_invalid')
                    _instant(attempt['at'])
                if len({(attempt['epoch'],attempt['generation']) for attempt in entry['attempts']})>1:
                    raise JournalError('journal_invalid')
                if any(_instant(left['at'])>_instant(right['at']) for left,right in zip(entry['attempts'],entry['attempts'][1:])):
                    raise JournalError('journal_invalid')
                if entry['attempts'] and (entry['attempts'][0]['generation']<minimum_generation or
                    previous_end is not None and _instant(entry['attempts'][0]['at'])<previous_end):
                    raise JournalError('journal_invalid')
                for stamp in entry['reconciliations']:_instant(stamp)
                if any(_instant(left)>_instant(right) for left,right in zip(entry['reconciliations'],entry['reconciliations'][1:])):
                    raise JournalError('journal_invalid')
                if entry['ack_at'] is not None:_instant(entry['ack_at'])
            except (ValueError,TypeError,JournalError):raise JournalError('journal_invalid') from None
            if ((entry['state']=='acknowledged') != (entry['ack_at'] is not None)
                or (entry['revision'] is not None and (type(entry['revision']) is not int or entry['revision']<0))
                or (entry['state']=='blocked') != (entry['blocked_reason'] is not None)
                or (entry['blocked_reason'] is not None and entry['blocked_reason'] not in {'target_conflict','authorization_changed','invalid_bundle','credential_revoked'})
                or (entry['state']=='prepared' and (entry['attempts'] or entry['reconciliations']))
                or (entry['state'] in {'unconfirmed','acknowledged','fenced','superseded'} and not entry['attempts'])
                or (entry['reconciliations'] and not entry['attempts'])
                or (entry['state'] in {'fenced','superseded'} and not entry['reconciliations'])
                or (entry['state'] in {'fenced','superseded'}) != (entry['fence'] is not None)
                or (entry['state']=='superseded') != (entry['successor'] is not None)
                or any(v is not None and (not isinstance(v,str) or not SHA.fullmatch(v)) for v in (entry['predecessor'],entry['successor']))):
                raise JournalError('journal_invalid')
            if entry['fence'] is not None:
                fence=entry['fence']
                if (not isinstance(fence,dict) or set(fence)!={'proof','generation','at'} or
                    fence['proof']!=reconciliation_proof(entry) or type(fence['generation']) is not int or
                    not entry['attempts'][0]['generation']<fence['generation']<=9007199254740991):
                    raise JournalError('journal_invalid')
                try:
                    if _instant(fence['at'])<_instant(entry['reconciliations'][-1]):raise JournalError('journal_invalid')
                except (ValueError,TypeError,JournalError):raise JournalError('journal_invalid') from None
        entries=value['entries']
        resolved=set()
        for key,entry in entries.items():
            predecessor=entries.get(entry['predecessor']) if entry['predecessor'] else None
            successor=entries.get(entry['successor']) if entry['successor'] else None
            if entry['predecessor'] and (not predecessor or predecessor['state']!='superseded' or
                predecessor['successor']!=key or predecessor['source_id']!=entry['source_id'] or
                predecessor['scope_id']!=entry['scope_id'] or not set(predecessor['message_ids'])<=set(entry['message_ids'])):
                raise JournalError('journal_invalid')
            if entry['successor'] and (not successor or successor['predecessor']!=key):
                raise JournalError('journal_invalid')
            seen=set();current=key
            while current and current not in resolved:
                if current in seen:raise JournalError('journal_invalid')
                seen.add(current);current=entries[current]['successor']
            resolved.update(seen)
        claimed=set()
        for entry in entries.values():
            if entry['state']=='superseded':continue
            keys={(entry['source_id'],message_id) for message_id in entry['message_ids']}
            if claimed & keys:raise JournalError('journal_invalid')
            claimed.update(keys)
        return value

    def _save(self,value):
        self._require_owner()
        encoded=json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()
        temporary=self.path.with_name(self.path.name+'.'+uuid4().hex+'.tmp')
        try:
            with temporary.open('xb') as output:
                output.write(encoded);output.flush();os.fsync(output.fileno())
            os.replace(temporary,self.path)
        finally:
            if temporary.exists():temporary.unlink()

    def prepare(self,metadata):
        with self.lock:
            if not _valid_metadata(metadata):raise JournalError('invalid_metadata')
            value=self._read();key=metadata['dedupe_key'];existing=value['entries'].get(key)
            if existing:
                if any(existing.get(k)!=v for k,v in metadata.items()):
                    raise JournalError('frozen_bundle_changed')
                return dict(existing)
            if any(e.get('raw_id')==metadata['raw_id'] for e in value['entries'].values()):
                raise JournalError('raw_id_already_tracked')
            if any(e.get('source_id')==metadata['source_id'] and set(e.get('message_ids',[])) & set(metadata['message_ids'])
                   for e in value['entries'].values()):
                raise JournalError('source_message_already_tracked')
            entry={**metadata,'state':'prepared','attempts':[],'reconciliations':[],'ack_at':None,'revision':None,'blocked_reason':None,
                   'fence':None,'predecessor':None,'successor':None,'history':[]}
            value['entries'][key]=entry;self._save(value)
            return dict(entry)

    def tracked_messages(self,*,exclude_fenced_key=None):
        with self.lock:
            entries=self._read()['entries']
            if exclude_fenced_key is not None and (exclude_fenced_key not in entries or entries[exclude_fenced_key]['state']!='fenced'):
                raise JournalError('fenced_entry_required')
            return [{key:entry[key] for key in ('source_id','raw_id','snapshot_id','message_ids','state')}
                    for key,entry in entries.items() if entry['state']!='superseded' and key!=exclude_fenced_key]

    def get(self,key):
        with self.lock:
            entry=self._read()['entries'].get(key)
            return json.loads(json.dumps(entry)) if entry else None

    def pending_entries(self):
        with self.lock:
            values=[entry for entry in self._read()['entries'].values() if entry['state'] in {'prepared','unconfirmed','fenced'}]
            return json.loads(json.dumps(values))

    def claim(self,key,*,at,daily_limit,cycle_limit,epoch,generation):
        if type(daily_limit) is not int or not 1<=daily_limit<=10000:
            raise JournalError('invalid_daily_limit')
        if type(cycle_limit) is not int or not 1<=cycle_limit<=daily_limit:
            raise JournalError('invalid_cycle_limit')
        if (not isinstance(epoch,str) or not IDENTITY.fullmatch(epoch) or type(generation) is not int
            or not 0<=generation<9007199254740991):
            raise JournalError('invalid_attempt_identity')
        at=_instant(at);day=at.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
        cycle=int(at.timestamp()//900)
        with self.lock:
            value=self._read();entry=value['entries'].get(key)
            if not entry or entry['state'] not in {'prepared','unconfirmed'}:
                raise JournalError('entry_not_retryable')
            seconds=60 if len(entry['records'])>4 else 900;cycle=int(at.timestamp()//seconds)
            if entry['attempts']:
                previous=entry['attempts'][-1]
                if (previous['epoch'],previous['generation'])!=(epoch,generation):
                    raise JournalError('retry_identity_changed')
                if _instant(previous['at'])>at:raise JournalError('clock_moved_backwards')
            elif entry['history']:
                last=entry['history'][-1]['fence']
                if generation<last['generation']:raise JournalError('attempt_fenced')
                if at<_instant(last['at']):raise JournalError('clock_moved_backwards')
            self._check_quota(value,day,cycle,daily_limit,cycle_limit,cycle_seconds=seconds)
            if len(entry['attempts'])>=10000:raise JournalError('attempt_history_limit')
            # Commit the unknown-outcome marker before any possible network call.
            entry['attempts'].append({'at':at.isoformat(),'epoch':epoch,'generation':generation});entry['state']='unconfirmed'
            self._save(value)
            return dict(entry)

    @staticmethod
    def _check_quota(value,day,cycle,daily_limit,cycle_limit,*,cycle_seconds=900):
        stamps=[attempt['at'] for entry in value['entries'].values() for attempt in entry['attempts']]
        stamps.extend(stamp for entry in value['entries'].values() for stamp in entry['reconciliations'])
        stamps.extend(attempt['at'] for entry in value['entries'].values() for prior in entry['history'] for attempt in prior['attempts'])
        stamps.extend(stamp for entry in value['entries'].values() for prior in entry['history'] for stamp in prior['reconciliations'])
        if sum(_instant(stamp).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()==day for stamp in stamps)>=daily_limit:
            raise JournalError('daily_limit_reached')
        if sum(int(_instant(stamp).timestamp()//cycle_seconds)==cycle for stamp in stamps)>=cycle_limit:
            raise JournalError('cycle_limit_reached')

    def claim_reconciliation(self,key,*,at,daily_limit,cycle_limit):
        if type(daily_limit) is not int or not 1<=daily_limit<=10000:raise JournalError('invalid_daily_limit')
        if type(cycle_limit) is not int or not 1<=cycle_limit<=daily_limit:raise JournalError('invalid_cycle_limit')
        at=_instant(at);day=at.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat();cycle=int(at.timestamp()//900)
        with self.lock:
            value=self._read();entry=value['entries'].get(key)
            if not entry or entry['state']!='unconfirmed':raise JournalError('entry_not_unconfirmed')
            seconds=60 if len(entry['records'])>4 else 900;cycle=int(at.timestamp()//seconds)
            last=entry['reconciliations'][-1] if entry['reconciliations'] else entry['attempts'][-1]['at']
            if _instant(last)>at:raise JournalError('clock_moved_backwards')
            if len(entry['reconciliations'])>=10000:raise JournalError('attempt_history_limit')
            self._check_quota(value,day,cycle,daily_limit,cycle_limit,cycle_seconds=seconds)
            entry['reconciliations'].append(at.isoformat());self._save(value)
            return dict(entry)

    def acknowledge(self,key,*,at,state,revision=None):
        if (state not in {'published','already_published'} or (state=='published' and revision is None)
            or (revision is not None and (type(revision) is not int or revision<0))):
            raise JournalError('invalid_receipt')
        with self.lock:
            value=self._read();entry=value['entries'].get(key)
            if not entry or entry['state']!='unconfirmed':raise JournalError('entry_not_unconfirmed')
            entry.update(state='acknowledged',ack_at=_instant(at).isoformat(),revision=revision)
            self._save(value)
            return dict(entry)

    def record_fence(self,key,*,receipt,at):
        with self.lock:
            value=self._read();entry=value['entries'].get(key)
            if not entry or entry['state']!='unconfirmed':raise JournalError('entry_not_unconfirmed')
            if not entry['reconciliations']:raise JournalError('reconciliation_not_claimed')
            if (not isinstance(receipt,dict) or set(receipt)!={'state','proof','generation'} or
                receipt['state']!='fenced_absent' or receipt['proof']!=reconciliation_proof(entry) or
                type(receipt['generation']) is not int or
                not entry['attempts'][0]['generation']<receipt['generation']<=9007199254740991):
                raise JournalError('invalid_fence_receipt')
            at=_instant(at)
            if at<_instant(entry['attempts'][-1]['at']) or (entry['reconciliations'] and at<_instant(entry['reconciliations'][-1])):
                raise JournalError('clock_moved_backwards')
            entry['state']='fenced';entry['fence']={'proof':receipt['proof'],'generation':receipt['generation'],'at':at.isoformat()}
            self._save(value)
            return dict(entry)

    def rearm_fenced(self,key,metadata):
        """Requeue the identical Raw only after durable proof the old attempt is fenced."""
        with self.lock:
            if not _valid_metadata(metadata):raise JournalError('invalid_metadata')
            value=self._read();entry=value['entries'].get(key)
            if not entry or entry['state']!='fenced':raise JournalError('fenced_entry_required')
            if any(entry.get(field)!=metadata.get(field) for field in META):raise JournalError('frozen_bundle_changed')
            if len(entry['history'])>=1000 or entry['fence']['generation']>=9007199254740991:
                raise JournalError('attempt_history_limit')
            entry['history'].append({'attempts':entry['attempts'],'reconciliations':entry['reconciliations'],'fence':entry['fence']})
            entry.update(state='prepared',attempts=[],reconciliations=[],fence=None)
            self._save(value)
            return json.loads(json.dumps(entry))

    def replace_fenced(self,key,metadata,*,lineage_verified):
        """Atomically link a safely fenced old attempt to its verified successor."""
        with self.lock:
            if not _valid_metadata(metadata):raise JournalError('invalid_metadata')
            value=self._read();old=value['entries'].get(key)
            if not old or old['state']!='fenced':raise JournalError('fenced_entry_required')
            newkey=metadata['dedupe_key']
            if (newkey in value['entries'] or old['scope_id']!=metadata['scope_id'] or
                old['source_id']!=metadata['source_id'] or old['raw_id']==metadata['raw_id'] or
                old['snapshot_id']==metadata['snapshot_id'] or not set(old['message_ids'])<=set(metadata['message_ids']) or
                any(e['raw_id']==metadata['raw_id'] for e in value['entries'].values())):
                raise JournalError('invalid_successor')
            if any(e['state']!='superseded' and otherkey!=key and e['source_id']==metadata['source_id'] and
                set(e['message_ids']) & set(metadata['message_ids']) for otherkey,e in value['entries'].items()):
                raise JournalError('source_message_already_tracked')
            if not callable(lineage_verified) or lineage_verified(json.loads(json.dumps(old)),json.loads(json.dumps(metadata))) is not True:
                raise JournalError('successor_lineage_unverified')
            entry={**metadata,'state':'prepared','attempts':[],'reconciliations':[],'ack_at':None,'revision':None,'blocked_reason':None,
                   'fence':None,'predecessor':key,'successor':None,'history':[]}
            old['state']='superseded';old['successor']=newkey;value['entries'][newkey]=entry
            self._save(value)
            return dict(entry)

    def block(self,key,reason):
        if reason not in {'target_conflict','authorization_changed','invalid_bundle','credential_revoked'}:
            raise JournalError('invalid_block_reason')
        with self.lock:
            value=self._read();entry=value['entries'].get(key)
            if not entry or entry['state'] not in {'prepared','unconfirmed'}:
                raise JournalError('entry_not_blockable')
            entry.update(state='blocked',blocked_reason=reason)
            self._save(value)
            return dict(entry)
