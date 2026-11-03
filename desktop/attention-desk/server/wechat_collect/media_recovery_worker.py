"""Authorized saved-message media lookup. Never advances a collection cursor."""
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib,json,sys
from pathlib import Path
from .db_reader import make_reader,DBReadError,wechat_version,normalized_message
from .db_media import preserve_local_media

def run(request):
    if datetime.now(ZoneInfo('Asia/Shanghai')).hour<6:raise DBReadError('quiet')
    if request['chat'] not in request['allowed_chats'] or request['chatroom_id'] not in request['allowed_chatrooms']:raise DBReadError('chat_not_allowlisted')
    if wechat_version()!=request['verified_version']:raise DBReadError('wechat_version_changed_requires_verification')
    reader=make_reader(request['allowed_chats'],selected_chatroom=request['chatroom_id'],workdir=request['workdir'])
    room=reader.resolve(request['chat'])
    if room!=request['chatroom_id'] or hashlib.sha256(reader.account.encode()).hexdigest()[:24]!=request['account_scope']:raise DBReadError('database_account_or_chat_changed')
    results=[]
    for target in request['messages'][:8]:
        raw=reader.get_message_row(room,target['local_id'],shard=target['shard'])
        if not raw:results.append({'message_id':target['message_id'],'error':'source_message_missing'});continue
        # get_message_row preserves the shard sender namespace and safe content.
        raw.setdefault('local_type',raw.get('type_id',0))
        raw.setdefault('type',reader._msg_type_name(raw['local_type']))
        raw.setdefault('sender_id',raw.get('real_sender_id'))
        if isinstance(raw.get('content'),bytes):raw['content']=reader._friendly_content(raw['content'],raw.get('type'))
        normalized=normalized_message(reader,request['chat'],room,raw)
        if normalized['source_content_sha256']!=target['source_content_sha256']:
            results.append({'message_id':target['message_id'],'error':'source_message_changed'});continue
        # The immutable, already captured reference provides no new credentials.
        reference={**target['reference'],**(normalized.get('media_reference') or {})}
        message={**raw,'local_id':target['local_id'],'shard':target['shard'],'media_reference':reference}
        path=preserve_local_media(reader,room,message,request['workdir']+'/media')
        results.append({'message_id':target['message_id'],'path':path,'reference':reference})
    return {'items':results,'messages_sent':0,'unauthorized_message_bodies_read':0}

if __name__=='__main__':
    try:print(json.dumps({'ok':True,'result':run(json.loads(sys.stdin.buffer.read(200000)))},ensure_ascii=True))
    except Exception as error:print(json.dumps({'ok':False,'code':str(error) if isinstance(error,DBReadError) else 'media_lookup_failed'},ensure_ascii=True));sys.exit(1)
