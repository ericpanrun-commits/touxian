"""Concrete Windows source/evidence boundary for a reviewed cloud test.

This never creates an approval, migrates a database, or sends a request. The
original full-sync hold must remain true. All four local evidence files and the
separate cloud approval are leased until the synchronous transport handoff.
"""
from contextlib import ExitStack,contextmanager
import hashlib
import json
from pathlib import Path

import yaml

from ..wechat_collect.config import SettingsDocument
from ..wechat_collect.deepseek_content_gate import read_grant,FILENAME
from .deepseek_dispatch import ReviewedCloudGrant
from .deepseek_preview import build_deepseek_preview
from .settings import WorkerSettings
from .wechat_dispatch_authority import DispatchDenied,locked_source
from .wechat_file_lease import immutable_evidence


EVIDENCE_FILES=('recovery-controls.json','cloud-worker-settings.json','wechat.yaml',FILENAME)


def evidence_digest(files):
    if set(files)!=set(EVIDENCE_FILES):
        raise DispatchDenied('required_evidence_missing')
    return hashlib.sha256(json.dumps({name:hashlib.sha256(files[name]).hexdigest()
        for name in EVIDENCE_FILES},sort_keys=True,separators=(',',':')).encode()).hexdigest()


def cloud_approval(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate_approval_key')
            result[key]=value
        return result
    value=json.loads(raw,object_pairs_hook=unique)
    if (not isinstance(value,dict) or set(value)!={'version','approved','authorization_record','grant'}
            or type(value['version']) is not int or value['version']!=1 or value['approved'] is not True
            or not isinstance(value['authorization_record'],str) or not value['authorization_record'].strip()):
        raise DispatchDenied('separate_cloud_approval_required')
    return ReviewedCloudGrant(**value['grant'])


class ReviewedLocalEvidence:
    def __init__(self,directory,approval_path,*,approval_sha256,settings=None):
        self.directory=Path(directory).resolve()
        self.approval_path=Path(approval_path).resolve()
        self.approval_sha256=approval_sha256
        self.settings=settings or WorkerSettings(self.directory/'cloud-worker-settings.json')

    @contextmanager
    def files(self,grant):
        with ExitStack() as stack:
            raw=stack.enter_context(immutable_evidence(self.approval_path,max_bytes=16384))
            if hashlib.sha256(raw).hexdigest()!=self.approval_sha256 or cloud_approval(raw)!=grant:
                raise DispatchDenied('cloud_approval_changed')
            files={name:stack.enter_context(immutable_evidence(self.directory/name,max_bytes=65536)) for name in EVIDENCE_FILES}
            if evidence_digest(files)!=grant.evidence_hash:
                raise DispatchDenied('cloud_evidence_changed')
            controls=json.loads(files['recovery-controls.json'])
            if (controls.get('version')!=1 or controls.get('hold_cloud_uploads') is not True
                    or controls.get('pause_paid_ai') is not True):
                raise DispatchDenied('global_holds_required')
            # Settings are decrypted only after the separately approved cloud
            # descriptor and all pinned evidence have passed validation.
            if self.settings.path.resolve()!=self.directory/'cloud-worker-settings.json':
                raise DispatchDenied('cloud_settings_path_changed')
            config=self.settings.read()
            deepseek=read_grant(files[FILENAME])
            document=SettingsDocument.model_validate(yaml.safe_load(files['wechat.yaml']))
            yield deepseek,document,config

    @contextmanager
    def snapshot(self,grant):
        with self.files(grant) as (deepseek,document,config):
            def build(connection):
                preview=build_deepseek_preview(connection,deepseek,grant.job_id)
                # Select the raw record explicitly; record order is not a scope.
                source=next(r['payload']['source_id'] for r in preview['bundle']['records'] if r['kind']=='raw_item')
                if not document.wechat.enabled or not any('wechat:'+chat.id==source and chat.enabled
                        and document.wechat.allows(chat) for chat in document.wechat.priority_chats):
                    raise DispatchDenied('source_no_longer_enabled')
                return preview
            with locked_source(self.directory/'personal.sqlite3',build) as preview:
                yield preview,config
