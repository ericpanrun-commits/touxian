from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.wechat_collect.config import WeChatConfig
from server.wechat_collect.scheduler import SchedulerPolicy


def script(name):
    path=Path(__file__).resolve().parents[2]/'scripts'/f'{name}.py'
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def policy(hour=10):
    return SchedulerPolicy(WeChatConfig(enabled=True),lambda:datetime.fromisoformat(f'2026-10-22T{hour:02d}:00:00+08:00'))


def test_qt_probe_cannot_attach_or_import_uia_during_quiet_hours(monkeypatch):
    probe=script('wechat_qt_accessibility_probe')
    monkeypatch.setattr(probe,'desktop_binding',lambda *a:pytest.fail('No desktop API at night'))
    result=probe.run(probe.PROJECT/'artifacts/pyweixin-spike/offline-qt-quiet.json',attach=True,show=True,policy=policy(3))
    assert result['error_code']=='quiet' and not result['windows']
    assert result['messages_sent']==result['screenshots']==result['hklm_writes']==0


def test_root_inspection_only_reads_structural_fields():
    probe=script('wechat_qt_accessibility_probe')
    class Control:
        def __init__(self,cls,identity=''):
            self.ClassName=cls;self.identity=identity;self.FrameworkId='Qt';self.ControlTypeName='GroupControl'
        @property
        def Name(self):pytest.fail('Private Name getter must not be used')
        @property
        def Value(self):pytest.fail('Private Value getter must not be used')
    root=Control('mmui::MainWindow')
    children=[Control('mmui::ChatSessionCell'),Control('mmui::ChatInputField','chat_input_field')]
    class Results:
        def __init__(self,rows):self.rows=rows;self.Length=len(rows)
        def GetElement(self,index):return self.rows[index]
    class Element:
        def FindAll(self,scope,condition):
            rows=([root] if scope==7 else [])+children
            if condition is not True:
                prop,value=condition
                rows=[row for row in rows if (row.ClassName if prop==1 else row.identity)==value]
            return Results(rows)
    root.Element=Element()
    client=SimpleNamespace(CreateTrueCondition=lambda:True,CreatePropertyCondition=lambda prop,value:(prop,value))
    auto=SimpleNamespace(PropertyId=SimpleNamespace(ClassNameProperty=1,AutomationIdProperty=2),
                         Control=SimpleNamespace(CreateControlFromElement=lambda value:value))
    result=probe.inspect_root(root,client,auto,policy())
    assert result['class_counts']['mmui::MainWindow']==1
    assert result['class_counts']['mmui::ChatSessionCell']==1
    assert result['automation_id_counts']['chat_input_field']==1
    assert result['mmui_count']==3 and result['descendant_count']==2
    assert all(set(row)=={'class_name','framework_id','control_type'} for row in result['structure'])


def test_restart_scope_rejects_unrelated_process_before_shutdown(tmp_path,monkeypatch):
    restart=script('wechat_qt_accessibility_pass')
    executable=tmp_path/'Weixin.exe';executable.write_bytes(b'synthetic executable identity')
    shutdown=[];ended=[]
    class Function:
        def __init__(self,call):self.call=call
        def __call__(self,*args):return self.call(*args)
    def query_image(handle,flags,buffer,length):buffer.value=str(executable);return 1
    def start(session,flags,key):session._obj.value=7;return 0
    def listed(session,needed,count,infos,reboot):
        needed._obj.value=2
        if infos is None:return 234
        count._obj.value=2;infos[0].process.pid=42;infos[1].process.pid=999
        return 0
    kernel=SimpleNamespace(OpenProcess=Function(lambda *a:100),CloseHandle=Function(lambda *a:1),
        GetProcessTimes=Function(lambda *a:1),QueryFullProcessImageNameW=Function(query_image),
        GetExitCodeProcess=Function(lambda *a:pytest.fail('Scope must fail before shutdown')))
    rm=SimpleNamespace(RmStartSession=Function(start),RmEndSession=Function(lambda *a:ended.append(a)),
        RmRegisterResources=Function(lambda *a:0),RmGetList=Function(listed),
        RmShutdown=Function(lambda *a:shutdown.append(a)),RmRestart=Function(lambda *a:pytest.fail('No restart')))
    monkeypatch.setattr(restart.ctypes,'WinDLL',lambda name,**kw:kernel if name=='kernel32' else rm)
    with pytest.raises(RuntimeError,match='unapproved'):
        restart.graceful_restart(42,[{'pid':42,'path':str(executable)}],policy())
    assert shutdown==[] and ended


@pytest.mark.parametrize('failure', ['expired', 'helper_exited', 'value_removed', 'rolled_back'])
def test_hklm_diagnostic_requires_live_lease_and_actual_registry_value(tmp_path,monkeypatch,failure):
    experiment=script('wechat_force_uia_pass')
    monkeypatch.setattr(experiment,'ROOT',tmp_path)
    expiry=datetime.now(timezone.utc)+timedelta(seconds=-1 if failure=='expired' else 300)
    (tmp_path/'status.json').write_text(json.dumps({
        'state':'rolled_back' if failure=='rolled_back' else 'applied',
        'pid':42,'lease_expires_at':expiry.isoformat()}),encoding='utf-8')
    monkeypatch.setattr(experiment,'helper_is_running',lambda pid:failure!='helper_exited')
    monkeypatch.setattr(experiment,'read_force_uia',lambda view:
        {'key_exists':False,'value_exists':False} if failure=='value_removed' else
        {'key_exists':True,'value_exists':True,'value':1,'kind':experiment.winreg.REG_DWORD})
    with pytest.raises(RuntimeError):
        experiment.require_active_lease()
