from dataclasses import asdict,replace
import hashlib
import json
from uuid import uuid4

import pytest
import yaml

from server.tests_wechat.test_deepseek_dispatch import personal,setup,step
from server.cloud_sync.deepseek_cloud_evidence import ReviewedLocalEvidence,evidence_digest,EVIDENCE_FILES
from server.cloud_sync.settings import WorkerSettings
from server.cloud_sync.wechat_dispatch_authority import Grant


@pytest.fixture
def bound(setup):
    s=setup;directory=s['personal'].path.parent
    document=yaml.safe_load((directory/'wechat.yaml').read_text(encoding='utf8'))
    document['wechat']['allowed_chats']=[chat['name'] for chat in document['wechat']['priority_chats']]
    (directory/'wechat.yaml').write_text(yaml.safe_dump(document,allow_unicode=True),encoding='utf8')
    (directory/'recovery-controls.json').write_text(json.dumps({'version':1,'hold_cloud_uploads':True,'pause_paid_ai':True}),encoding='utf8')
    settings={k:v for k,v in s['config'].items() if k!='worker_key'}
    settings.update(protected_worker_key=s['config']['worker_key'],notes_installation_id=str(uuid4()))
    (directory/'cloud-worker-settings.json').write_text(json.dumps(settings),encoding='utf8')
    files={name:(directory/name).read_bytes() for name in EVIDENCE_FILES}
    s['grant']=replace(s['grant'],approval_id=str(uuid4()),evidence_hash=evidence_digest(files))
    approval=directory/'synthetic-cloud-approval.json'
    approval.write_text(json.dumps({'version':1,'approved':True,'authorization_record':'synthetic fixture only',
        'grant':asdict(s['grant'])}),encoding='utf8')
    evidence=ReviewedLocalEvidence(directory,approval,approval_sha256=hashlib.sha256(approval.read_bytes()).hexdigest(),
        settings=WorkerSettings(directory/'cloud-worker-settings.json',protector=lambda value,**_:value))
    s['authority'].import_verified(Grant(s['grant'].approval_id,s['grant'].binding(),
        s['clock'][0].timestamp()+14400,False),lambda candidate:candidate.binding==s['grant'].binding())
    s['sender'].protected_preview=evidence.snapshot
    s['sender'].current=lambda candidate:candidate==s['grant']
    s['approval']=approval;s['evidence']=evidence
    return s


def test_concrete_file_leases_and_database_boundary_work_with_existing_transport(bound):
    s=bound;observed=[]
    def edit_at_handoff():
        for path in [s['approval'],*(s['personal'].path.parent/name for name in EVIDENCE_FILES)]:
            with pytest.raises(OSError):
                path.write_bytes(b'changed')
            observed.append(path.name)
    s['backend'].accept_hook=edit_at_handoff
    assert step(s)=='acknowledged'
    assert len(observed)==5


@pytest.mark.parametrize('name',[*EVIDENCE_FILES,'approval'])
def test_changed_file_during_connect_prevents_any_bytes(bound,name):
    s=bound
    path=s['approval'] if name=='approval' else s['personal'].path.parent/name
    s['backend'].prepare_hook=lambda:path.write_bytes(path.read_bytes()+b' ')
    assert step(s) in {'cloud_approval_changed','cloud_evidence_changed'}
    assert s['backend'].sent==[]


def test_deepseek_authorization_cannot_replace_separate_cloud_consent(bound):
    s=bound;s['approval'].unlink()
    assert step(s)=='protected_preview_unavailable'
    assert s['backend'].sent==[]
