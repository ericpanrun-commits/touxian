from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from server.tests_wechat.conftest import personal
from server.tests_wechat.test_deepseek_cloud_preview import analyzed,preview
from server.cloud_sync.wechat_outbound_validation import _safe_text,validate_outbound
from server.personal_store import encode

CASES=json.loads((Path(__file__).resolve().parents[1]/'fixtures/wechat/cloud-public-policy-cases.json').read_text(encoding='utf8'))


@pytest.mark.parametrize('case',CASES)
def test_impersonal_policy_exception_never_authorizes_names_or_contacts(case):
    assert _safe_text(case['text']) is case['safe']


@pytest.mark.parametrize('state',['pending','archived','ignored','accepted'])
def test_review_status_is_preserved_consistently_in_all_four_records(personal,state):
    _,job=analyzed(personal)
    bundle=preview(personal,job)['bundle']
    rows={r['kind']:deepcopy(r) for r in bundle['records']}
    rows['raw_review']['payload']['status']=state
    rows['inbox_item']['payload']['review_status']=state
    rows['inbox']['payload']['review_status']=state
    rows['inbox']['payload']['review']['status']=state
    for row in rows.values():row['source_hash']=hashlib.sha256(encode(row['payload']).encode()).hexdigest()
    args=(rows,rows['raw_item']['id'],rows['raw_review']['id'],rows['raw_item']['payload']['source_id'])
    if state in {'pending','archived'}:validate_outbound(*args)
    else:
        with pytest.raises(ValueError):validate_outbound(*args)
    if state=='archived':
        rows['inbox_item']['payload']['review_status']='pending'
        with pytest.raises(ValueError):validate_outbound(*args)


def test_policy_is_kept_in_wire_text_not_redacted_or_rewritten(personal):
    text='请全体同学提前确认参加时间。\n违规报名将被列入黑名单。'
    _,job=analyzed(personal,text=text)
    result=preview(personal,job)
    assert result['cloud_outbound_validation']=='passed'
    assert result['samples']['samples'][0]['payload']['text']==text
    assert result['upload_authorized'] is False
