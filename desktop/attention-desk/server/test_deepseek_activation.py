import json
from pathlib import Path
import runpy

import pytest

from server.tests_wechat.conftest import personal
from server.tests_wechat.test_deepseek_worker import prepared
from server.wechat_collect.deepseek_content_gate import FILENAME
from server.wechat_collect.deepseek_worker import CONTROLS


def module():
    return runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/enable-deepseek-text-worker.py'))


def test_activation_preview_pins_scope_without_changing_controls_or_history(personal,monkeypatch):
    prepared(personal,monkeypatch)
    with personal.connection(write=True) as connection:
        connection.execute('UPDATE wechat_chat SET binding_json=?',
            (json.dumps({'account_scope':'synthetic-account','internal_chat_id':'synthetic@chatroom'}),))
    names=[FILENAME,CONTROLS,'recovery-controls.json','wechat.yaml']
    before={name:(personal.path.parent/name).read_bytes() for name in names}
    result=module()['prepare'](personal.path.parent,['test'],'explicit-synthetic-approval')
    assert result['chat_watermarks']=={'test':1}
    assert set(result['source_bindings'])=={'test'}
    assert result['mode']=='continuous'
    assert {name:(personal.path.parent/name).read_bytes() for name in names}==before
    with personal.connection() as connection:
        assert connection.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==0
        assert connection.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0]==0


@pytest.mark.parametrize('ids',[['test','test'],['unapproved']])
def test_activation_cannot_expand_or_duplicate_source_scope(personal,monkeypatch,ids):
    prepared(personal,monkeypatch)
    with pytest.raises(ValueError): module()['prepare'](personal.path.parent,ids,'synthetic')


def test_activation_does_not_guess_unbound_source_identity(personal,monkeypatch):
    prepared(personal,monkeypatch)
    with personal.connection(write=True) as connection:
        connection.execute("UPDATE wechat_chat SET binding_json='{}'")
    with pytest.raises(ValueError,match='verified_source_binding_required'):
        module()['prepare'](personal.path.parent,['test'],'synthetic')


def test_activation_cannot_overwrite_revoked_evidence(tmp_path):
    target=tmp_path/'authorization.json'
    target.write_text('{"enabled":false}',encoding='utf8')
    with pytest.raises(FileExistsError): module()['exclusive_json'](target,{'enabled':True})
    assert json.loads(target.read_text())['enabled'] is False
