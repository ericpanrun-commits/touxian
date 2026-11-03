"""Owned image evidence and cached local transcription. No screen capture API."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from ..documents.storage import AttachmentStore
from ..personal_store import PersonalError, encode, identifier, now


class MediaStore:
    def __init__(self,personal,policy):
        self.personal,self.policy=personal,policy
        self.files=AttachmentStore(personal)
        self.spool=personal.path.parent/'wechat-spool'

    def preserve(self,message_id,path,*,capture_kind='screenshot',override=None):
        self.policy.require(operation='media',maximum_seconds=10,override=override)
        path=Path(path).resolve()
        if not path.is_relative_to(self.spool.resolve()) or not path.is_file():
            raise PersonalError('图片不属于采集暂存区。','invalid_media_path',400)
        if capture_kind not in {'original','screenshot','local_cache'}:
            raise PersonalError('未知图片来源。')
        with self.personal.connection() as c:
            if not c.execute('SELECT 1 FROM wechat_message WHERE id=?',(message_id,)).fetchone():
                raise PersonalError('没有经过确认的新增图片消息。','message_required',409)
        with Image.open(path) as image:
            width,height=image.size
            image_extension={'PNG':'png','JPEG':'jpg','WEBP':'webp','GIF':'gif'}.get(image.format)
            if not image_extension:
                raise PersonalError('图片格式暂不支持，原文件保留。','image_format',409)
            if width*height>30_000_000:
                raise PersonalError('图片过大，原文件留在暂存区。','image_too_large',409)
            pixels=list(image.convert('L').resize((8,8)).get_flattened_data())
            average=sum(pixels)/64
            phash=f'{sum((1<<i) for i,value in enumerate(pixels) if value>=average):016x}'
        saved,digest,_=self.files.preserve(path,image_extension)
        with self.personal.connection(write=True) as c:
            old=c.execute('SELECT id FROM wechat_media WHERE message_id=? AND sha256=?',(message_id,digest)).fetchone()
            if old:
                return old[0]
            media_id=identifier()
            revision=c.execute('SELECT coalesce(max(revision),-1)+1 FROM wechat_media WHERE message_id=?',(message_id,)).fetchone()[0]
            c.execute('INSERT INTO wechat_media(id,message_id,sha256,phash,local_path,capture_kind,width,height,revision,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                      (media_id,message_id,digest,phash,saved,capture_kind,width,height,revision,now()))
            return media_id

    def ocr(self,media_id,*,engine=None,engine_version='windows.media.ocr-v2',override=None):
        # Even cache/parse entrypoints obey the one policy; status endpoints never call here.
        self.policy.require(operation='ocr',maximum_seconds=120,override=override)
        with self.personal.connection() as c:
            row=c.execute('SELECT * FROM wechat_media WHERE id=?',(media_id,)).fetchone()
            if not row:
                raise PersonalError('图片不存在。','not_found',404)
            media=dict(row)
            cache_table=c.execute("SELECT 1 FROM sqlite_master WHERE name='wechat_media_parse_cache'").fetchone()
            failed=c.execute("SELECT status FROM wechat_media_parse_cache WHERE sha256=? AND engine='ocr' AND version=?",(media['sha256'],engine_version)).fetchone() if cache_table else None
            if failed and failed['status']=='failed':
                return {'status':'needs_review','code':'ocr_failed','cache_hit':True,'original_preserved':True,'media_id':media_id}
            cached=c.execute("SELECT * FROM wechat_media WHERE sha256=? AND engine=? AND status='parsed' ORDER BY revision DESC LIMIT 1",(media['sha256'],engine_version)).fetchone()
            if not cached:
                cached=c.execute("SELECT * FROM wechat_media WHERE sha256=? AND engine='deepseek-vision' AND status='parsed' ORDER BY revision DESC LIMIT 1",(media['sha256'],)).fetchone()
        if cached:
            blocks=json.loads(cached['blocks_json'])
        else:
            if engine is None:
                engine=lambda path,work:self._native_ocr(path,work,override=override)
            try:
                work=self.personal.path.parent/'wechat-ocr-work'/media_id
                work.mkdir(parents=True,exist_ok=True)
                blocks,_=engine(media['local_path'],work)
                if not blocks:
                    raise ValueError('empty transcription')
            except Exception:
                with self.personal.connection(write=True) as c:
                    if media['status']!='parsed':
                        c.execute("UPDATE wechat_media SET status='needs_review',error_code='ocr_failed' WHERE id=?",(media_id,))
                    if cache_table:c.execute("INSERT OR REPLACE INTO wechat_media_parse_cache VALUES (?,'ocr',?,'failed','[]','{}',?)",(media['sha256'],engine_version,now()))
                return {'status':'needs_review','code':'ocr_failed','original_preserved':True}
        with self.personal.connection(write=True) as c:
            if cache_table:
                from .quality import usable_text
                c.execute("INSERT OR REPLACE INTO wechat_media_parse_cache VALUES (?,'ocr',?,?,?,?,?)",(media['sha256'],engine_version,'parsed' if usable_text('\n'.join(b['text'] for b in blocks)) else 'weak',encode(blocks),encode({'cache_hit':bool(cached)}),now()))
            engine_name=cached['engine'] if cached else engine_version
            if media['status']=='parsed' and (media['engine']!=engine_name or media['blocks_json']!=encode(blocks)):
                # Do not change a transcription version already referenced by Evidence.
                revision=c.execute('SELECT COALESCE(MAX(revision),0)+1 FROM wechat_media WHERE message_id=?',(media['message_id'],)).fetchone()[0]
                new_id=identifier()
                c.execute('INSERT INTO wechat_media(id,message_id,sha256,phash,local_path,capture_kind,width,height,status,revision,engine,blocks_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (new_id,media['message_id'],media['sha256'],media['phash'],media['local_path'],media['capture_kind'],media['width'],media['height'],'parsed',revision,engine_name,encode(blocks),now()))
                media_id=new_id
            else:
                c.execute("UPDATE wechat_media SET status='parsed',blocks_json=?,engine=?,error_code=NULL WHERE id=?",(encode(blocks),engine_name,media_id))
        return {'status':'parsed','cache_hit':bool(cached),'blocks':blocks,'media_id':media_id,'original_preserved':True}

    def _native_ocr(self,path,work,*,override=None):
        # Existing parser containment also kills its owned PowerShell OCR children.
        import os
        import subprocess
        import sys
        self.policy.require(operation='ocr',maximum_seconds=60,override=override)
        # Work on a derived input only. Tiny WeChat thumbnails otherwise fall
        # below the native recognizer's minimum glyph size.
        path=Path(path)
        scale=1
        with Image.open(path) as original:
            if min(original.size)<400:
                scale=min(4, max(1, 1000/max(original.size)))
                resized=original.convert('RGB').resize((round(original.width*scale),round(original.height*scale)),Image.Resampling.LANCZOS)
                enlarged=Path(work)/'ocr-input.png'
                resized.save(enlarged)
                path=enlarged
        result=Path(work)/'ocr.json'
        subprocess.run([sys.executable,'-m','server.documents.worker',str(path),Path(path).suffix.lstrip('.'),str(result)],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                       timeout=45,check=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        value=json.loads(result.read_text(encoding='utf-8'))
        if value.get('error'):
            raise ValueError('local OCR unavailable')
        if scale!=1:
            for block in value['blocks']:
                for box in [block.get('bounding_box'),*[word.get('box') for word in block.get('words',[])]]:
                    if box:
                        for key in ('x','y','width','height','image_width','image_height'):
                            if key in box:box[key]/=scale
            value.setdefault('metadata',{})['ocr_input_scale']=scale
        return value['blocks'],value.get('metadata',{})
