"""Metadata-only discovery; message shards are excluded before reader construction."""
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .db_reader import DBReadError, make_reader, search_group_metadata, wechat_version

def main():
    try:
        request=json.loads(sys.stdin.buffer.read(10000))
        if datetime.now(ZoneInfo('Asia/Shanghai')).hour<6:
            raise DBReadError('quiet')
        if wechat_version()!=request['verified_version']:
            raise DBReadError('wechat_version_changed_requires_verification')
        query=request.get('query','')
        if not isinstance(query,str) or not 1<=len(query.strip())<=120:
            raise DBReadError('invalid_group_query')
        reader=make_reader([],workdir=request['workdir'],metadata_only=True)
        result=search_group_metadata(reader,query.strip(),limit=30,offset=max(0,min(int(request.get('offset',0)),3000)),chatroom_id=request.get('chatroom_id'))
        if reader.boundary.authorized_body_reads:
            raise DBReadError('metadata_boundary_violation')
        sys.stdout.write(json.dumps({'ok':True,'result':result},ensure_ascii=True))
        return 0
    except Exception as exc:
        sys.stdout.write(json.dumps({'ok':False,'code':str(exc) if isinstance(exc,DBReadError) else 'metadata_search_failed'}))
        return 1

if __name__=='__main__':
    raise SystemExit(main())
