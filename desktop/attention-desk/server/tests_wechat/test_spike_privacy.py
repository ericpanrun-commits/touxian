from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace

from server.wechat_collect import spike,windows
from server.wechat_collect.windows import DesktopState


def test_name_queries_allow_spaces_and_brackets_without_fuzzy_private_search():
    names=spike.group_name_variants('合成26班（公告群）')
    assert set(names)=={'合成26班（公告群）','合成26班(公告群)','合成26班 公告群','合成26班公告群'}
    assert all('26' in name and '公告群' in name for name in names)
    assert len(names)<=6


def test_allowlisted_name_never_bypasses_night_gate(monkeypatch):
    def forbidden():
        raise AssertionError('Night diagnostics must not inspect processes')
    monkeypatch.setattr(spike,'_process_metadata',forbidden)
    report=spike.inspect_structure(clock=lambda:datetime.fromisoformat('2026-10-22T01:00:00+08:00'),chat_name='合成指定群')
    assert report['real_operations']==report['desktop_state_checks']==0


def fake_uia(monkeypatch,states):
    target='合成指定群'
    class Window:
        ProcessId=42
        ClassName='FakeWeChatWindow'
        FrameworkId='FakeFramework'
        ControlTypeName='WindowControl'
        NativeWindowHandle=100
        IsOffscreen=False
        def GetChildren(self): return []
        @property
        def Name(self): raise AssertionError('Arbitrary names must never be read')
    class OtherPrivateWindow:
        ProcessId=99
        def __getattr__(self,key): raise AssertionError('Unrelated window content must not be read')
    class Matches:
        Length=1
        def GetElement(self,index): return Window()
    def find_all(scope,condition):
        assert scope==7 and condition==(30005,target)
        return Matches()
    window=Window()
    window.Element=SimpleNamespace(FindAll=find_all)
    client=SimpleNamespace(CreatePropertyCondition=lambda field,value:(field,value))
    fake=SimpleNamespace(UIAutomationInitializerInThread=lambda:nullcontext(),
                         GetRootControl=lambda:SimpleNamespace(GetChildren=lambda:[OtherPrivateWindow(),window]),
                         PropertyId=SimpleNamespace(NameProperty=30005),
                         Control=SimpleNamespace(CreateControlFromElement=lambda element:element),
                         uiautomation=SimpleNamespace(_AutomationClient=SimpleNamespace(instance=lambda:SimpleNamespace(IUIAutomation=client))))
    import sys
    monkeypatch.setitem(sys.modules,'uiautomation',fake)
    monkeypatch.setattr(windows,'require_idle',lambda *args:next(states))
    monkeypatch.setattr(spike,'_process_metadata',lambda:[{'pid':42,'file_version':'synthetic-version'}])
    return target


def test_diagnostic_filters_exact_group_name_without_reading_private_names(monkeypatch):
    stable=DesktopState(180,True,1,(0,0))
    target=fake_uia(monkeypatch,iter([stable]*8))
    report=spike.inspect_structure(clock=lambda:datetime.fromisoformat('2026-10-22T10:00:00+08:00'),chat_name=target)
    assert report['matrix']['window_location']['status']=='pass'
    assert report['matrix']['target_group_binding']['status']=='partial'
    assert report['matrix']['focus_unchanged']['status']=='pass'
    assert report['level']=='NO_GO' and report['runtime_verified'] is False
    assert report['safety']['write_operations']==report['safety']['messages_sent']==0


def test_diagnostic_stops_if_foreground_changes(monkeypatch):
    before=DesktopState(180,True,1,(0,0))
    changed=DesktopState(180,True,2,(0,0))
    fake_uia(monkeypatch,iter([before,changed]))
    report=spike.inspect_structure(clock=lambda:datetime.fromisoformat('2026-10-22T10:00:00+08:00'))
    assert report['interrupted_for_activity']
    assert report['matrix']['window_location']['status']=='not_tested'
    assert report['safety']['messages_sent']==0


def test_opaque_uia_and_msaa_shell_never_gets_a_go_certificate():
    node={'patterns':{'text':False,'selection':False,'scroll':False},'msaa':{'structure':[{'role':10,'child_count':0}]}}
    report={'matrix':{},'windows':[{'structure':[node],'target_match_count':0,'is_foreground':True}],'level':'NO_GO','runtime_verified':False}
    spike.summarize_readability(report)
    assert report['matrix']['target_group_binding']['status']=='fail'
    assert report['matrix']['foreground']['status']=='partial'
    assert report['level']=='NO_GO' and not report['runtime_verified']
