import hashlib
import json
from pathlib import Path

import pytest

from server.ai_settings import AIConfig
from server.extraction_errors import ExtractionFailure
from server.tests_wechat.test_pipeline import output_for
from server.wechat_collect.analysis import build_text_request
from server.wechat_collect.deepseek_projection import prepare_reviewed_projection, retained_positions


def fixture(original='明天提交报告。', reviewed=None):
    message = {'message_id':'private-message', 'block_id':'private-block', 'raw_text':original,
        'sender':'private-sender', 'message_type':'text', 'source_time':'2026-11-08T10:00:00+08:00',
        'time_reliability':'reliable'}
    batch = {'id':'private-batch', 'primary_ids':['private-message']}
    snapshot = {'blocks':[{'block_id':'private-block', 'text':original, 'source_type':'text'}],
        'wechat':{'messages':[message], 'warnings':['private-warning']}}
    approved = [{'original_sha256':hashlib.sha256(original.encode()).hexdigest(),
        'text':original if reviewed is None else reviewed, 'source_time':message['source_time'],
        'time_reliability':'reliable'}]
    return batch, snapshot, approved


def output(projection):
    return output_for({'batch_id':projection.batch['id'],
        'blocks':projection.snapshot['blocks'], 'messages':projection.snapshot['wechat']['messages']})


def test_request_omits_identifiers_sender_history_media_and_profile():
    batch, snapshot, approved = fixture()
    snapshot['blocks'].append({'block_id':'private-history', 'text':'private-context', 'source_type':'text'})
    snapshot['wechat']['messages'].append({'message_id':'old', 'block_id':'private-history',
        'raw_text':'private-context', 'sender':'private-old-sender', 'message_type':'text'})
    snapshot['blocks'].append({'block_id':'private-media', 'text':'private-attachment', 'source_type':'image_ocr'})
    projection = prepare_reviewed_projection(batch, snapshot, approved)
    request = json.dumps(build_text_request(AIConfig(), projection.batch, projection.snapshot, projection.context))
    assert 'private-' not in request
    assert projection.context['notes'] == '' and projection.context['role_assertions'] == {}
    assert len(projection.snapshot['blocks']) == 1
    restored, _ = projection.restore(output(projection), {})
    assert restored['batch_id'] == 'private-batch'
    assert restored['evidence'][0]['block_id'] == 'private-block'
    assert restored['evidence'][0]['message_id'] == 'private-message'
    assert restored['items'][0]['evidence_message_ids'] == ['private-message']


@pytest.mark.parametrize('change', ['text','time','reliability','media','missing_grant','source_block'])
def test_unapproved_changes_fail_closed(change):
    batch, snapshot, approved = fixture()
    message = snapshot['wechat']['messages'][0]
    if change == 'text':
        message['raw_text'] = snapshot['blocks'][0]['text'] = '另一条未授权通知'
    elif change == 'time': message['source_time'] = '2026-11-09T10:00:00+08:00'
    elif change == 'reliability': message['time_reliability'] = 'unverified'
    elif change == 'media': message['message_type'] = 'image'
    elif change == 'missing_grant': approved.clear()
    elif change == 'source_block': snapshot['blocks'][0]['text'] = 'different'
    with pytest.raises(ExtractionFailure):
        prepare_reviewed_projection(batch, snapshot, approved)


def test_removed_contact_line_restores_retained_quote_offsets():
    original = '联系人：虚构甲 13800000000\n请全体同学提交报告。'
    reviewed = '请全体同学提交报告。'
    projection = prepare_reviewed_projection(*fixture(original, reviewed))
    restored, _ = projection.restore(output(projection), {})
    evidence = restored['evidence'][0]
    assert evidence['start'] == original.index(reviewed)
    assert original[evidence['start']:evidence['end']] == evidence['quote'] == reviewed


def test_quote_cannot_bridge_removed_private_content():
    projection = prepare_reviewed_projection(*fixture('活动通知\n联系人：虚构甲\n明天集合', '活动通知\n明天集合'))
    with pytest.raises(ExtractionFailure) as exc:
        projection.restore(output(projection), {})
    assert exc.value.code == 'projection_quote_crosses_removed_content'


@pytest.mark.parametrize('original,reviewed', [('楼内\n楼内', '楼内'), ('原始文字', '改写文字')])
def test_ambiguous_occurrence_or_rewritten_text_cannot_map(original, reviewed):
    with pytest.raises(ExtractionFailure): retained_positions(original, reviewed)


def test_unchanged_multiline_unicode_spans_and_adjacent_lines():
    original = '联系人：虚构甲\n通知🏫\n明天集合'
    reviewed = '通知🏫\n明天集合'
    positions = retained_positions(original, reviewed)
    assert positions == list(range(original.index('通知'), len(original)))


def test_local_role_can_restrict_without_profile_in_request():
    projection = prepare_reviewed_projection(*fixture('请班长提交报告。'))
    restored, _ = projection.restore(output(projection),
        {'role_assertions':{'班长':{'value':'false', 'confirmed':True}}})
    assert not restored['items'][0]['action_required']
    assert restored['items'][0]['relevant'] is False


def test_saved_course_deadline_remaps_anchor_without_losing_date():
    projection = prepare_reviewed_projection(*fixture('同学们，请在下周二中午12点前完成高等数学作业。'))
    response = json.loads((Path(__file__).resolve().parents[1]/'fixtures/wechat/deepseek-h01-response.json').read_text(encoding='utf8'))
    response['batch_id'] = projection.batch['id']
    restored, _ = projection.restore(response, {})
    deadline = restored['items'][0]['deadline']
    assert deadline['anchor_message_id'] == 'private-message'
    assert deadline['value'] == '2026-11-10T12:00'


def test_reference_restoration_preserves_unresolved_semantics():
    projection = prepare_reviewed_projection(*fixture('老师确认了，改到二田。'))
    response = output(projection)
    response['items'][0].update(information_complete=False, await_more_context=True,
        action_required=False, actions=[], category='course_change')
    restored, _ = projection.restore(response, {})
    assert restored['items'][0]['await_more_context'] is True
    assert restored['_attention']['notice1']['bucket'] == 'unresolved'


def test_projected_response_keeps_existing_local_json_envelope_repair():
    projection = prepare_reviewed_projection(*fixture())
    raw='```json\n'+json.dumps(output(projection),ensure_ascii=False)+'\n```'
    restored,warnings=projection.restore(raw,{})
    assert restored['batch_id']=='private-batch'
    assert 'json_repaired_locally' in warnings
