from datetime import datetime
import importlib.util
from pathlib import Path

import pytest
from server.wechat_collect.config import WeChatConfig
from server.wechat_collect.scheduler import SchedulerPolicy

spec=importlib.util.spec_from_file_location('pyweixin_environment_spike',Path(__file__).resolve().parents[2]/'scripts/pyweixin_environment_spike.py')
env=importlib.util.module_from_spec(spec);spec.loader.exec_module(env)


class Backend:
    def __init__(self):self.reader=False;self.narrator=0
    def read(self):return {'screen_reader':self.reader,'narrator':self.narrator}
    def set_screen_reader(self,value):self.reader=value
    def set_narrator(self,value):self.narrator=value


def policy(hour=10):
    return SchedulerPolicy(WeChatConfig(enabled=True),lambda:datetime.fromisoformat(f'2026-10-22T{hour:02d}:00:00+08:00'))


def test_settings_restore_even_when_api_test_fails():
    backend=Backend();report={}
    with pytest.raises(RuntimeError):
        with env.temporary_accessibility(backend,policy(),report):
            assert backend.reader and backend.narrator==1
            raise RuntimeError('API failed')
    assert backend.read()=={'screen_reader':False,'narrator':0}
    assert report['restoration_matches']


def test_partial_application_failure_still_restores_screen_reader():
    backend=Backend()
    def failed(value):raise OSError('Registry write unavailable')
    backend.set_narrator=failed
    with pytest.raises(OSError):
        with env.temporary_accessibility(backend,policy(),{}):pytest.fail('Must not run')
    assert not backend.reader


def test_night_gate_does_not_read_or_modify_environment():
    class Forbidden:
        def read(self):pytest.fail('Night environment probe')
    with pytest.raises(Exception):
        with env.temporary_accessibility(Forbidden(),policy(3),{}):pytest.fail('Must not run')
