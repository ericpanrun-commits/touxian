"""Authorized notes-only persistence stays separate from browser pairing."""
import asyncio,copy,json,socket
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from server.collector.security import PairingAuthority,CollectorError
from server.collector.controller import CollectorController
from server.test_collector import rig,network,FakeServer


def test_notes_verifier_survives_only_authorized_restart_and_expires_or_revokes(tmp_path):
    tick=[datetime(2026,10,28,tzinfo=timezone.utc)]
    path=tmp_path/'notes-verifier.json';a=PairingAuthority(clock=lambda:tick[0]);a.restore_notes(path)
    key=a.issue_notes();assert key not in path.read_text()
    assert a.notes_session(key).expires==tick[0]+timedelta(days=90)
    with pytest.raises(CollectorError):a.session(key)
    a.revoke('collector_shutdown',preserve_notes=True)
    b=PairingAuthority(clock=lambda:tick[0]);b.restore_notes(path)
    assert b.notes_session(key).owner.startswith('notes-')
    with pytest.raises(CollectorError):b.notes_session(json.loads(path.read_text())['digest'])
    tick[0]+=timedelta(days=90)
    with pytest.raises(CollectorError):b.notes_session(key)
    tick[0]-=timedelta(days=90);b.revoke('collector_stopped')
    c=PairingAuthority(clock=lambda:tick[0]);c.restore_notes(path)
    assert not c.notes_ready()


def test_authorized_autostart_only_on_the_exact_private_wifi_and_manual_stop_revokes(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            current=network();current['interfaces'][0].update(wireless=True,network_id='isolated-wifi',interface_guid='isolated-guid')
            class Listener:
                def setsockopt(self,*a):pass
                def bind(self,value):bindings.append(value)
                def listen(self,*a):pass
                def close(self):pass
            bindings=[]
            monkeypatch.setattr('server.collector.controller.socket',SimpleNamespace(socket=lambda *a:Listener(),AF_INET=socket.AF_INET,SOCK_STREAM=socket.SOCK_STREAM,SOL_SOCKET=socket.SOL_SOCKET,SO_EXCLUSIVEADDRUSE=getattr(socket,'SO_EXCLUSIVEADDRUSE',4)))
            def controller():return CollectorController(r.store,r.docs,r.extraction,tmp_path/'app',detector=lambda port:copy.deepcopy(current),server_factory=FakeServer)
            first=controller();first.interface_id='3'
            with pytest.raises(CollectorError):first.authorize_notes_auto(True,'explicit_local_ui','old text-only scope')
            status=await asyncio.to_thread(first.authorize_notes_auto,True,'explicit_user_chat','允许上述范围，减少重配')
            assert status['enabled'] and status['notes_auto_enabled'] and status['notes_maximum_days']==90
            verifier=first.authority.notes_grant['digest']
            await first.close()
            second=controller();assert not second.status()['enabled']
            await asyncio.to_thread(second.resume_notes)
            assert second.status()['enabled'] and second.authority.notes_grant['digest']==verifier
            await second.close()
            current['interfaces'][0]['network_id']='different-private-wifi'
            third=controller();await asyncio.to_thread(third.resume_notes)
            assert not third.status()['enabled'] and not third.authority.notes_ready()
            assert not third.status()['notes_auto_enabled']
            await third.close()
    asyncio.run(run())
