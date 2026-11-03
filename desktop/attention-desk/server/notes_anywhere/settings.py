"""One notes-only consumer credential, protected by the existing Windows DPAPI."""
import json
import re
from pathlib import Path
from uuid import UUID
from ..ai_settings import protect
from ..personal_store import PersonalError

PROJECT='mantuehuzptactymjpqg'
BASE=f'https://{PROJECT}.supabase.co'
ENDPOINT=BASE+'/functions/v1/eric-notes-ingest'
MAX_BYTES=50_000_000


class NotesSettings:
    def __init__(self,path,*,protector=protect):self.path=Path(path);self.protect=protector

    def read(self):
        if not self.path.exists():return None
        try:
            value=json.loads(self.path.read_text(encoding='utf-8'))
            if value['endpoint']!=ENDPOINT or str(UUID(value['installation_id']))!=value['installation_id'] or type(value.get('enabled')) is not bool:raise ValueError()
            token=self.protect(value['protected_sync_key'],decrypt=True)
            if not re.fullmatch(r'esync_[A-Za-z0-9_-]{43}',token):raise ValueError()
            return {**value,'sync_key':token}
        except (OSError,ValueError,KeyError,TypeError):
            raise PersonalError('笔记云端连接无法读取，已保留原件和待接收记录。','notes_connection_error',503) from None

    def save(self,installation_id,sync_key,*,enabled=True):
        installation_id=str(UUID(installation_id))
        if not re.fullmatch(r'esync_[A-Za-z0-9_-]{43}',sync_key):raise PersonalError('笔记同步连接无效。','invalid_notes_connection',422)
        value={'endpoint':ENDPOINT,'installation_id':installation_id,'protected_sync_key':self.protect(sync_key),'enabled':bool(enabled)}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp');temporary.write_text(json.dumps(value),encoding='utf-8');temporary.replace(self.path)
        return {'configured':True,'enabled':bool(enabled)}
