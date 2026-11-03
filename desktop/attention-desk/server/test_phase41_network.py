"""Probe failures close intake without rotating the authorized note connection."""
import asyncio
import copy
import json
import socket
import subprocess
from types import SimpleNamespace

import pytest

from server.collector.controller import CollectorController
from server.collector.security import CollectorError
from server.test_collector import FakeServer, network, rig


def wifi():
    value=network()
    value['interfaces'][0].update(wireless=True,network_id='fixture-wifi',interface_guid='fixture-guid')
    return value


UNKNOWN={'interfaces':[],'firewall':{'checked':False},'inspection_failed':True}


class Trigger:
    def __init__(self):self.stopped=False
    def wait(self,seconds):return self.stopped
    def is_set(self):return self.stopped
    def set(self):self.stopped=True


def sockets(monkeypatch):
    bindings=[];closed=[]
    class Listener:
        def setsockopt(self,*a):pass
        def bind(self,value):bindings.append(value)
        def listen(self,*a):pass
        def close(self):closed.append(True)
    monkeypatch.setattr('server.collector.controller.socket',SimpleNamespace(socket=lambda *a:Listener(),
        AF_INET=socket.AF_INET,SOCK_STREAM=socket.SOCK_STREAM,SOL_SOCKET=socket.SOL_SOCKET,
        SO_EXCLUSIVEADDRUSE=getattr(socket,'SO_EXCLUSIVEADDRUSE',4)))
    return bindings,closed


@pytest.mark.parametrize('missing',['probe_failure','profile','wifi_name'])
def test_unknown_probe_closes_listener_then_resumes_same_note_grant_only_on_exact_wifi(tmp_path,monkeypatch,missing):
    async def run():
        async with rig(tmp_path) as r:
            bindings,closed=sockets(monkeypatch)
            current=[wifi()]
            controller=CollectorController(r.store,r.docs,r.extraction,tmp_path/'app',detector=lambda port:copy.deepcopy(current[0]),server_factory=FakeServer)
            controller.interface_id='3'
            before=await asyncio.to_thread(controller.authorize_notes_auto,True,'explicit_user_chat','允许上述范围，减少重配')
            grant=copy.deepcopy(controller.authority.notes_grant)
            qr=controller.notes_qr(before['notes_setup_id'])
            token=controller.authority.regenerate();cookie,_=controller.authority.pair(token)
            controller.stop_watch.set();trigger=Trigger();controller.stop_watch=trigger
            unknown=copy.deepcopy(UNKNOWN) if missing=='probe_failure' else wifi()
            if missing=='profile':unknown['interfaces'][0]['profile']='Unknown'
            if missing=='wifi_name':unknown['interfaces'][0]['network_id']=''
            checks=[0]
            def detector(port):
                checks[0]+=1
                if checks[0]==1:return copy.deepcopy(unknown)
                assert controller.server is None and controller.listener is None and closed
                assert controller.authority.notes_grant==grant
                with pytest.raises(CollectorError):controller.authority.session(cookie)
                return wifi()
            controller.detector=detector
            try:
                await asyncio.to_thread(controller._watch_network,trigger)
                assert controller.status()['enabled'] and len(bindings)==2
                assert bindings[0]==bindings[1]
                assert controller.authority.notes_grant==grant
                assert controller.notes_qr(before['notes_setup_id'])==qr
                assert json.loads(controller.settings_path.read_text())['notes_auto']['enabled']
            finally:await controller.close()
    asyncio.run(run())


@pytest.mark.parametrize('change',['ssid','guid','ip','public','disconnected'])
def test_confirmed_network_change_after_unknown_probe_revokes_instead_of_resuming(tmp_path,monkeypatch,change):
    async def run():
        async with rig(tmp_path) as r:
            bindings,_=sockets(monkeypatch)
            controller=CollectorController(r.store,r.docs,r.extraction,tmp_path/'app',detector=lambda port:wifi(),server_factory=FakeServer)
            controller.interface_id='3'
            await asyncio.to_thread(controller.authorize_notes_auto,True,'explicit_user_chat','允许上述范围，减少重配')
            controller.stop_watch.set();trigger=Trigger();controller.stop_watch=trigger
            changed=wifi();entry=changed['interfaces'][0]
            if change=='ssid':entry['network_id']='another-wifi'
            elif change=='guid':entry['interface_guid']='another-adapter'
            elif change=='ip':entry['address']='192.168.50.7'
            elif change=='public':entry['profile']='Public'
            else:changed['interfaces']=[]
            probes=[copy.deepcopy(UNKNOWN),changed]
            controller.detector=lambda port:copy.deepcopy(probes.pop(0) if probes else changed)
            try:
                await asyncio.to_thread(controller._watch_network,trigger)
                assert not controller.status()['enabled'] and len(bindings)==1
                assert not controller.authority.notes_ready()
                assert not json.loads(controller.settings_path.read_text())['notes_auto']['enabled']
            finally:await controller.close()
    asyncio.run(run())


def test_manual_stop_while_waiting_for_probe_prevents_automatic_resume(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            bindings,_=sockets(monkeypatch)
            controller=CollectorController(r.store,r.docs,r.extraction,tmp_path/'app',detector=lambda port:wifi(),server_factory=FakeServer)
            controller.interface_id='3'
            await asyncio.to_thread(controller.authorize_notes_auto,True,'explicit_user_chat','允许上述范围，减少重配')
            controller.stop_watch.set();trigger=Trigger();controller.stop_watch=trigger
            checks=[0]
            def detector(port):
                checks[0]+=1
                if checks[0]==1:return copy.deepcopy(UNKNOWN)
                controller.stop(False)
                return wifi()
            controller.detector=detector
            try:
                await asyncio.to_thread(controller._watch_network,trigger)
                assert len(bindings)==1 and not controller.status()['enabled']
                assert not controller.authority.notes_ready() and not controller.notes_auto['enabled']
            finally:await controller.close()
    asyncio.run(run())


def test_unknown_probe_at_restart_preserves_grant_and_stays_closed_until_verified(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            bindings,_=sockets(monkeypatch)
            def make(detector):return CollectorController(r.store,r.docs,r.extraction,tmp_path/'app',detector=detector,server_factory=FakeServer)
            first=make(lambda port:wifi());first.interface_id='3'
            await asyncio.to_thread(first.authorize_notes_auto,True,'explicit_user_chat','允许上述范围，减少重配')
            grant=copy.deepcopy(first.authority.notes_grant)
            await first.close()
            second=make(lambda port:copy.deepcopy(UNKNOWN))
            try:
                await asyncio.to_thread(second.resume_notes)
                assert second.server is None and len(bindings)==1
                assert second.authority.notes_grant==grant and second.notes_auto['enabled']
                second.stop_watch.set();trigger=Trigger();second.stop_watch=trigger
                second.detector=lambda port:wifi()
                await asyncio.to_thread(second._watch_network,trigger,second.notes_auto['network'])
                assert second.status()['enabled'] and len(bindings)==2
                assert second.authority.notes_grant==grant
            finally:await second.close()
    asyncio.run(run())


def test_another_unknown_probe_before_rebind_keeps_waiting_with_same_grant(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            bindings,_=sockets(monkeypatch)
            controller=CollectorController(r.store,r.docs,r.extraction,tmp_path/'app',detector=lambda port:wifi(),server_factory=FakeServer)
            controller.interface_id='3'
            await asyncio.to_thread(controller.authorize_notes_auto,True,'explicit_user_chat','允许上述范围，减少重配')
            grant=copy.deepcopy(controller.authority.notes_grant)
            controller.stop_watch.set();trigger=Trigger();controller.stop_watch=trigger
            probes=[copy.deepcopy(UNKNOWN),wifi(),copy.deepcopy(UNKNOWN),wifi(),wifi()]
            controller.detector=lambda port:copy.deepcopy(probes.pop(0) if probes else wifi())
            try:
                await asyncio.to_thread(controller._watch_network,trigger)
                assert controller.status()['enabled'] and len(bindings)==2
                assert controller.authority.notes_grant==grant
            finally:await controller.close()
    asyncio.run(run())


def test_ordinary_collector_has_no_unapproved_retry_after_unknown_probe(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            sockets(monkeypatch)
            controller=CollectorController(r.store,r.docs,r.extraction,tmp_path/'app',detector=lambda port:wifi(),server_factory=FakeServer)
            await asyncio.to_thread(controller.enable,'3',8473)
            controller.authority.issue_notes()
            controller.stop_watch.set();trigger=Trigger();controller.stop_watch=trigger
            controller.detector=lambda port:copy.deepcopy(UNKNOWN)
            try:
                await asyncio.to_thread(controller._watch_network,trigger)
                assert trigger.is_set() and not controller.authority.notes_ready()
                assert controller.server is None
            finally:await controller.close()
    asyncio.run(run())


def test_direct_enable_cannot_move_preserved_note_grant_to_another_private_wifi(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            bindings,_=sockets(monkeypatch)
            current=[wifi()]
            controller=CollectorController(r.store,r.docs,r.extraction,tmp_path/'app',detector=lambda port:copy.deepcopy(current[0]),server_factory=FakeServer)
            controller.interface_id='3'
            await asyncio.to_thread(controller.authorize_notes_auto,True,'explicit_user_chat','允许上述范围，减少重配')
            await asyncio.to_thread(controller.stop,False,reason='network_unverified')
            current[0]['interfaces'][0]['network_id']='another-wifi'
            try:
                with pytest.raises(CollectorError):await asyncio.to_thread(controller.enable,'3',8473)
                assert len(bindings)==1 and not controller.authority.notes_ready()
            finally:await controller.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure',['timeout','missing_interfaces'])
def test_failed_physical_network_probe_is_explicitly_unknown(monkeypatch,failure):
    from server.collector import network as module
    monkeypatch.setattr(module.sys,'platform','win32')
    monkeypatch.setattr(module.shutil,'which',lambda name:'pwsh')
    def run(*a,**kw):
        if failure=='timeout':raise subprocess.TimeoutExpired('fixture',10)
        return SimpleNamespace(returncode=0,stdout=b'{}')
    monkeypatch.setattr(module.subprocess,'run',run)
    actual=module.discover()
    assert actual['inspection_failed'] is True and actual['interfaces']==[]
