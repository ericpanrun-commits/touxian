from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest

from server.personal_store import PersonalError
from server.wechat_collect.config import WeChatConfig
from server.wechat_collect.scheduler import SchedulerPolicy
from server.wechat_collect import visual_spike as visual


def policy(at='2026-10-22T10:00:00+08:00'):
    return SchedulerPolicy(WeChatConfig(enabled=True),clock=lambda:datetime.fromisoformat(at))


@pytest.mark.parametrize('header',[
    '合成测试群（公告群）','合成测试群(公告群)','合成测试群 公告群',
    '合成测试群（公告群）（120）','合成测试群(公告群) (120)',
])
def test_corrected_brackets_and_spaces_keep_group_identity(header):
    assert visual.exact_group_header(header,'合成测试群（公告群）')


@pytest.mark.parametrize('header',[
    '合成测试群（闲聊群）','合成测试群2（公告群）','合成测试群（公告群）新群',
    '合成测试群（公告）','其他合成测试群（公告群）','',
])
def test_group_matching_never_ignores_distinguishing_content(header):
    assert not visual.exact_group_header(header,'合成测试群（公告群）')


def test_crop_is_header_only_and_dpi_scaled():
    assert visual.header_region(1596,1242,144,'window_top')==(319,0,1596,48)
    assert visual.header_region(960,840,144,'window_top')==(192,0,960,48)
    assert visual.header_region(1277,1046,144)==(425,0,1277,96)
    assert visual.header_region(960,840,144)==(320,0,960,96)
    with pytest.raises(PersonalError,match='未截图'):
        visual.header_region(400,800,144)


def test_quiet_header_probe_never_inspects_windows_or_ocr(monkeypatch,tmp_path):
    def forbidden(*args,**kwargs):
        pytest.fail('Quiet diagnostics must not touch Windows or OCR')
    monkeypatch.setattr(visual,'capture_header',forbidden)
    monkeypatch.setattr(visual,'recognize_header',forbidden)
    report=visual.inspect_header(123,'合成测试群',tmp_path,policy('2026-10-22T03:00:00+08:00'))
    assert report['header_captures']==report['ocr_calls']==0
    assert report['error_code']=='quiet'
    assert not list(tmp_path.iterdir())


def test_even_matched_header_cannot_certify_message_reader(monkeypatch,tmp_path):
    monkeypatch.setattr(visual,'capture_header',lambda *args:{'synthetic':True})
    monkeypatch.setattr(visual,'recognize_header',lambda *args:{'group_match':True,'ocr_calls':1})
    report=visual.inspect_header(123,'合成测试群',tmp_path,policy())
    assert report['target_header']['status']=='partial'
    assert report['level']=='NO_GO' and not report['runtime_verified']
    assert report['message_bodies_read']==report['deepseek_calls']==report['vision_calls']==0


def test_blank_crop_skips_ocr(monkeypatch):
    monkeypatch.setattr(visual.subprocess,'run',lambda *args,**kwargs:pytest.fail('Blank crops must not run OCR'))
    assert visual.recognize_header({'blank':True},'合成测试群',policy())['ocr_calls']==0


def test_ocr_uses_content_specific_workspace_and_cached_result(monkeypatch,tmp_path):
    path=tmp_path/'header-test-content.png'
    path.write_bytes(b'synthetic-image')
    capture={'blank':False,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    monkeypatch.setattr(visual,'_output_directory',lambda value:Path(value))
    monkeypatch.setattr(visual,'require_idle',lambda *args:None)
    calls=[]
    def parse(command,**kwargs):
        calls.append(command)
        assert kwargs['timeout']==45
        Path(command[-1]).write_text(json.dumps({'blocks':[{'text':'合成测试群(公告群)'}]}),encoding='utf-8')
    monkeypatch.setattr(visual.subprocess,'run',parse)
    first=visual.recognize_header(capture,'合成测试群（公告群）',policy())
    cached=visual.recognize_header(capture,'合成测试群（公告群）',policy())
    assert first['group_match'] and first['ocr_calls']==1
    assert cached['group_match'] and cached['ocr_calls']==0
    assert len(calls)==1 and Path(calls[0][-1]).parent.name=='header-test-content-ocr'
    assert 'text' not in first
    path.write_bytes(b'changed-image')
    with pytest.raises(PersonalError) as error:
        visual.recognize_header(capture,'合成测试群',policy())
    assert error.value.code=='image_changed'


def test_diagnostics_cannot_write_outside_artifact_directory(tmp_path):
    with pytest.raises(PersonalError) as error:
        visual._output_directory(tmp_path/'unexpected.png')
    assert error.value.code=='invalid_output_path'


def test_unknown_capture_mode_rejected_before_native_import(monkeypatch,tmp_path):
    from server.wechat_collect.windows import DesktopState
    monkeypatch.setattr(visual,'require_idle',lambda *args:DesktopState(180,True,999,(0,0)))
    with pytest.raises(PersonalError) as error:
        visual.capture_header(123,'合成测试群',tmp_path,policy(),'unverified-mode')
    assert error.value.code=='invalid_capture_mode'
    assert not list(tmp_path.iterdir())


def test_visible_header_rejects_even_one_occluding_pixel():
    def owner(x,y):
        return 999 if (x,y)==(17,9) else 123
    with pytest.raises(PersonalError) as error:
        visual._verify_visible_region((10,3,30,12),owner,123,lambda:None)
    assert error.value.code=='header_occluded'


def test_visible_header_stops_on_activity_before_more_pixel_checks():
    seen=[]
    def owner(x,y):
        seen.append((x,y))
        return 123
    def changed():
        raise PersonalError('activity','activity_changed',409)
    with pytest.raises(PersonalError) as error:
        visual._verify_visible_region((0,0,10,10),owner,123,changed)
    assert error.value.code=='activity_changed' and seen==[]


@pytest.mark.parametrize('child_pid,origin,size',[
    (999,(100,100),(1000,800)),(123,(101,100),(1000,800)),(123,(100,100),(999,800)),
])
def test_render_child_must_match_parent_process_and_geometry(child_pid,origin,size):
    with pytest.raises(PersonalError) as error:
        visual._validate_render_surface(123,child_pid,(100,100),origin,(1000,800),size)
    assert error.value.code=='render_surface_mismatch'
