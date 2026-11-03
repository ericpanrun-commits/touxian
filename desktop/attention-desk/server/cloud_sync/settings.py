"""A separately revocable Windows processor identity, encrypted by Windows DPAPI."""
from __future__ import annotations
import hashlib
import json
import re
import secrets
from pathlib import Path
from uuid import UUID,uuid4
from ..ai_settings import protect
from ..personal_store import PersonalError

ENDPOINT='https://mantuehuzptactymjpqg.supabase.co/functions/v1/eric-app-api'


class WorkerSettings:
    def __init__(self,path,*,protector=protect):self.path=Path(path);self.protect=protector

    def read(self):
        if not self.path.exists():return None
        try:
            value=json.loads(self.path.read_text(encoding='utf-8'))
            if value['endpoint']!=ENDPOINT or type(value['enabled']) is not bool:raise ValueError()
            for field in ('processor_id','workspace_id','notes_installation_id'):UUID(value[field])
            token=self.protect(value['protected_worker_key'],decrypt=True)
            if not re.fullmatch(r'ework_[A-Za-z0-9_-]{43}',token):raise ValueError()
            return {**value,'worker_key':token}
        except (OSError,ValueError,KeyError,TypeError):
            raise PersonalError('此电脑的连接暂时无法读取，原件和待处理内容仍然保留。','worker_connection_error',503) from None

    def _write(self,value):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value),encoding='utf-8');temporary.replace(self.path)

    def prepare(self,workspace_id,notes_installation_id):
        workspace_id=str(UUID(workspace_id));notes_installation_id=str(UUID(notes_installation_id))
        existing=self.read()
        if existing:
            if (existing['workspace_id'],existing['notes_installation_id'])!=(workspace_id,notes_installation_id):
                raise PersonalError('此电脑已连接另一个 Eric OS，现有连接保留。','worker_identity_conflict',409)
            value=existing
        else:
            token='ework_'+secrets.token_urlsafe(32)
            value={'endpoint':ENDPOINT,'processor_id':str(uuid4()),'workspace_id':workspace_id,
                'notes_installation_id':notes_installation_id,'protected_worker_key':self.protect(token),'enabled':False}
            self._write(value);value={**value,'worker_key':token}
        # The registration descriptor cannot authenticate. The credential never leaves this process unencrypted.
        return {'id':value['processor_id'],'workspace_id':workspace_id,'label':'Windows Eric OS',
                'key_hash':hashlib.sha256(value['worker_key'].encode()).hexdigest()}

    def enable(self):
        value=self.read()
        if not value:raise PersonalError('此电脑尚未建立连接。','worker_not_configured',409)
        value.pop('worker_key',None);value['enabled']=True;self._write(value)
