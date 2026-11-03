"""Immutable message blocks -> the existing Raw Inbox, never formal entities."""
from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from datetime import timedelta

from ..personal_store import encode, identifier, now
from ..review_store import ReviewStore


def prepare_batch(personal, chat, scan_id, plan, *, at, delay_seconds=0, connection=None, media_revisions=None):
    batch_id, raw_id, stamp = identifier(), identifier(), now()
    ids = set(plan.primary) | set(plan.context)
    with (nullcontext(connection) if connection is not None else personal.connection(write=True)) as c:
        messages = [dict(r) for r in c.execute('SELECT * FROM wechat_message WHERE chat_id=? ORDER BY ingest_seq',(chat['id'],)) if r['id'] in ids]
        if not messages:
            return None
        # Previously claimed primary versions are context only, never re-analysed as new.
        if media_revisions is not None:
            if set(media_revisions)!=set(plan.primary) or any(m['message_type'] not in {'image','file'} for m in messages if m['id'] in plan.primary) or any(not isinstance(v,int) or v<1 for v in media_revisions.values()):
                raise ValueError('Media revisions may only reprocess explicitly changed media')
            claimed={mid for mid,revision in media_revisions.items() if c.execute("SELECT 1 FROM wechat_batch_message WHERE message_id=? AND content_revision=? AND role='primary'",(mid,revision)).fetchone()}
        else:
            claimed = {r[0] for r in c.execute("SELECT message_id FROM wechat_batch_message WHERE role='primary'")}
        primary = set(plan.primary) - claimed
        if not primary:
            return None
        original_parts=[]
        for message in messages:
            original_parts.append(message['raw_text'] or '[图片或附件内容尚未取得]')
            media=c.execute("SELECT blocks_json FROM wechat_media WHERE message_id=? AND status='parsed' ORDER BY revision DESC LIMIT 1",(message['id'],)).fetchone()
            if media:
                original_parts.extend(b['text'] for b in json.loads(media['blocks_json'] or '[]'))
        content = '\n\n'.join(original_parts)
        digest = hashlib.sha256(encode([[(m['id'],m['content_signature']) for m in messages],media_revisions] if media_revisions else [(m['id'],m['content_signature']) for m in messages]).encode()).hexdigest()
        c.execute('INSERT INTO raw_item(id,source_id,kind,submission_id,submission_hash,created_at) VALUES (?,?,?,?,?,?)',
                  (raw_id,chat['source_id'],'wechat_batch','wechat:'+batch_id,digest,stamp))
        snapshot_id = ReviewStore._new_snapshot(c,raw_id,content,stamp,None,chat['name']+' · 微信候选')
        warnings = ['context_budget_truncated'] if plan.context_truncated else []
        for ordinal,message in enumerate(messages):
            state=c.execute('SELECT error_code FROM wechat_message_state WHERE message_id=?',(message['id'],)).fetchone()
            if state and state['error_code']:
                warnings.append(state['error_code']+':'+message['id'])
            block_id = 'WX_'+message['id']
            text = message['raw_text'] or '[图片或附件内容尚未取得]'
            media = c.execute("SELECT * FROM wechat_media WHERE message_id=? ORDER BY revision DESC LIMIT 1",(message['id'],)).fetchone()
            attachment_id=None
            try:
                reference=json.loads(message.get('attachment_identity') or '{}')
                if not isinstance(reference,dict):reference={}
            except (TypeError,ValueError):reference={}
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='wechat_media_work'").fetchone():
                recovered=c.execute('SELECT reference_json FROM wechat_media_work WHERE message_id=?',(message['id'],)).fetchone()
                if recovered:reference.update(json.loads(recovered[0] or '{}'))
            if media:
                attachment_id=identifier()
                extension=__import__('pathlib').Path(media['local_path']).suffix.lstrip('.')
                c.execute('INSERT INTO attachment(id,raw_item_id,origin_snapshot_id,filename,local_path,mime_type,extension,size,sha256,downloaded_at,parse_status,version,extractor,parse_manifest_json,needs_review,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (attachment_id,raw_id,snapshot_id,reference.get('title') or '微信图片.'+extension,media['local_path'],{'png':'image/png','jpg':'image/jpeg','webp':'image/webp','gif':'image/gif'}.get(extension,'application/octet-stream'),extension,__import__('pathlib').Path(media['local_path']).stat().st_size,media['sha256'],stamp,'parsed' if media['status']=='parsed' else 'downloaded',1,media['engine'],encode({'metadata':{'capture_kind':media['capture_kind'],'evidence_tier':reference.get('evidence_tier'),'width':media['width'],'height':media['height'],'message_id':message['id']},'blocks':json.loads(media['blocks_json'] or '[]')}),1,stamp))
                c.execute('INSERT INTO snapshot_attachment VALUES (?,?)',(snapshot_id,attachment_id))
            elif message.get('attachment_identity'):
                # Local files and unsupported encrypted images are still durable
                # Evidence; decoding failure must not discard the original bytes.
                from pathlib import Path
                try:
                    if not isinstance(reference,dict):
                        raise ValueError('not a database media reference')
                    original=reference.get('evidence_path') or reference.get('encrypted_evidence_path')
                    path=Path(original).resolve() if original else None
                    if path and path.is_file() and path.is_relative_to((personal.path.parent/'files').resolve()):
                        attachment_id=identifier()
                        ext=path.suffix.lstrip('.')
                        name=reference.get('title') or ('微信图片.'+ext if reference.get('kind')=='image' else '微信附件.'+ext)
                        digest=reference.get('evidence_sha256') or reference.get('encrypted_evidence_sha256')
                        c.execute('INSERT INTO attachment(id,raw_item_id,origin_snapshot_id,filename,local_path,mime_type,extension,size,sha256,downloaded_at,parse_status,version,needs_review,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                  (attachment_id,raw_id,snapshot_id,name,str(path),__import__('mimetypes').guess_type(name)[0] or 'application/octet-stream',ext,path.stat().st_size,digest,stamp,'downloaded',1,1,stamp))
                        c.execute('INSERT INTO snapshot_attachment VALUES (?,?)',(snapshot_id,attachment_id))
                except (ValueError,TypeError):
                    pass
            c.execute('INSERT INTO raw_source_block(raw_snapshot_id,block_id,attachment_id,source_type,ordinal,text,section,needs_review) VALUES (?,?,?,?,?,?,?,?)',
                      (snapshot_id,block_id,attachment_id,'text',ordinal*100,text,'微信消息',int(message['message_type']!='text')))
            c.execute('INSERT INTO wechat_snapshot_message VALUES (?,?,?,?,?)',(snapshot_id,block_id,message['id'],media['id'] if media else None,0))
            if media and media['status']=='parsed':
                for index,block in enumerate(json.loads(media['blocks_json'] or '[]')):
                    derived_id = block_id+'_MEDIA_'+str(index)
                    c.execute('INSERT INTO raw_source_block(raw_snapshot_id,block_id,attachment_id,source_type,ordinal,text,section,bounding_box_json,raw_ocr_text,needs_review) VALUES (?,?,?,?,?,?,?,?,?,1)',
                              (snapshot_id,derived_id,attachment_id,'image_vision' if media['engine']=='deepseek-vision' else 'image_ocr',ordinal*100+index+1,block['text'],'视觉观察 · 非原文' if block.get('is_description') else media['capture_kind'],encode(block.get('bounding_box')),block.get('raw_ocr_text',block['text'])))
                    c.execute('INSERT INTO wechat_snapshot_message VALUES (?,?,?,?,?)',(snapshot_id,derived_id,message['id'],media['id'],media['revision']))
            elif message['message_type'] in {'image','file'}:
                warnings.append('media_unavailable:'+message['id'])
        ready = (at+timedelta(seconds=delay_seconds)).isoformat()
        c.execute('INSERT INTO wechat_batch(id,chat_id,raw_item_id,snapshot_id,scan_id,input_hash,status,ready_at,warnings_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                  (batch_id,chat['id'],raw_id,snapshot_id,scan_id,digest,'waiting_context' if delay_seconds else 'pending',ready,encode(warnings),stamp))
        # Keep potentially useful originals visible even if credentials/media/model fail.
        pending={'id':'_pending','title':'微信原文待核对','summary':content[:160],
                 'important':False,'relevant':None,'action_required':False,'category':'notice',
                 'confidence':0,'deadline':None,'time_text':'','reason':'原文已保存，模型尚未完成分析。',
                 'needs_review':True,'analysis_origin':'local_pending'}
        c.execute('INSERT INTO wechat_candidate(id,batch_id,model_item_id,important,relevant,status,data_json,created_at) VALUES (?,?,?,?,?,?,?,?)',
                  (batch_id+':_pending',batch_id,'_pending',0,None,'unresolved',encode(pending),stamp))
        for ordinal,message in enumerate(messages):
            c.execute('INSERT INTO wechat_batch_message VALUES (?,?,?,?,?)',(batch_id,message['id'],(media_revisions or {}).get(message['id'],0),'primary' if message['id'] in primary else 'context',ordinal))
            if message['id'] in primary:
                c.execute("UPDATE wechat_message_state SET status='batched' WHERE message_id=?",(message['id'],))
        return batch_id


def snapshot_metadata(c, snapshot_id):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wechat_snapshot_message'").fetchone():
        return None  # Historical migration fixtures are read before v10 is applied.
    mappings = [dict(r) for r in c.execute('SELECT sm.*,m.raw_text,m.sender,m.source_time,m.time_reliability,m.message_type,m.captured_at,m.message_hash,m.chat_name_at_capture AS chat_name FROM wechat_snapshot_message sm JOIN wechat_message m ON m.id=sm.message_id WHERE sm.snapshot_id=? ORDER BY m.ingest_seq,sm.block_id',(snapshot_id,))]
    has_db_identity = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wechat_db_message'").fetchone()
    has_sender_identity = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wechat_sender_identity'").fetchone()
    if has_db_identity:
        for mapping in mappings:
            identity = c.execute('SELECT internal_chat_id,shard,local_id,server_id,sort_seq,create_time,secondary_fingerprint,media_reference_json FROM wechat_db_message WHERE message_id=?', (mapping['message_id'],)).fetchone()
            if identity:
                mapping['database_identity'] = dict(identity)
                sender = c.execute('SELECT data_json FROM wechat_sender_identity WHERE message_id=?',(mapping['message_id'],)).fetchone() if has_sender_identity else None
                if sender:
                    mapping['database_identity']['sender'] = json.loads(sender[0])
            attachment=c.execute('SELECT a.filename,a.downloaded_at FROM raw_source_block b JOIN attachment a ON a.id=b.attachment_id WHERE b.raw_snapshot_id=? AND b.block_id=?',(snapshot_id,mapping['block_id'])).fetchone()
            if attachment:
                mapping['attachment_name']=attachment['filename']
                mapping['attachment_available']=bool(attachment['downloaded_at'])
    if not mappings:
        return None
    batch = c.execute('SELECT id,scan_id,warnings_json,superseded_by FROM wechat_batch WHERE snapshot_id=?',(snapshot_id,)).fetchone()
    latest=c.execute('SELECT newer.id FROM raw_snapshot newer JOIN raw_snapshot current ON current.raw_item_id=newer.raw_item_id WHERE current.id=? ORDER BY newer.created_at DESC,newer.id DESC LIMIT 1',(snapshot_id,)).fetchone()
    return {'source':'wechat','batch_id':batch['id'] if batch else None,
            'messages':mappings,'warnings':json.loads(batch['warnings_json']) if batch else [],
            'superseded_by':batch['superseded_by'] if batch else None,
            'latest_snapshot_id':latest[0] if latest and latest[0]!=snapshot_id else None,
            'analysis': [json.loads(r[0]) for r in c.execute("SELECT data_json FROM wechat_candidate WHERE batch_id=? AND model_item_id!='_pending'",(batch['id'],))] if batch else []}
