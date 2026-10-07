"""Content-addressed originals and immutable document snapshots in the shared DB."""
import hashlib
import json
import os
from pathlib import Path
from ..personal_store import identifier, now, encode, PersonalError
from ..review_store import ReviewStore
from .policy import filename, extension, MAX_BYTES, DocumentError
from .parsers import split_text
from .blocks import document_warnings


class AttachmentStore:
    def __init__(self, personal):
        self.personal = personal
        self.root = personal.path.parent / 'files'
        self.work = personal.path.parent / 'document-work'
        self.root.mkdir(parents=True, exist_ok=True)
        self.work.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def seed(c, snapshot_id):
        web = c.execute('SELECT * FROM raw_snapshot_web WHERE raw_snapshot_id=?',(snapshot_id,)).fetchone()
        if not web: return
        raw = c.execute('SELECT raw_item_id FROM raw_snapshot WHERE id=?',(snapshot_id,)).fetchone()[0]
        # Images in an image-only notice are useful OCR inputs as well.
        inputs = json.loads(web['attachments_json'])
        if web['body_status']=='image_only':
            inputs += [{'url':i['url'],'filename':i.get('filename') or i['url'].split('/')[-1].split('?')[0]} for i in json.loads(web['images_json'])]
        for index, value in enumerate(inputs):
            url = value.get('url')
            if not url: continue
            existing = c.execute('SELECT id FROM attachment WHERE origin_snapshot_id=? AND original_url=?',(snapshot_id,url)).fetchone()
            if existing: continue
            name = filename(value.get('filename') or value.get('name') or value.get('text') or url.split('/')[-1].split('?')[0])
            att_id = identifier()
            c.execute('''INSERT INTO attachment (id,raw_item_id,origin_snapshot_id,filename,original_url,extension,parse_status,created_at)
                VALUES (?,?,?,?,?,?,'not_downloaded',?)''',(att_id,raw,snapshot_id,name,url,value.get('extension') or extension(name),now()))
            c.execute('INSERT INTO snapshot_attachment VALUES (?,?)',(snapshot_id,att_id))

    def seed_existing(self):
        with self.personal.connection(write=True) as c:
            for row in c.execute('SELECT raw_snapshot_id FROM raw_snapshot_web').fetchall(): self.seed(c,row[0])

    def get(self, att_id):
        with self.personal.connection() as c:
            row = c.execute('SELECT * FROM attachment WHERE id=?',(att_id,)).fetchone()
            if not row: raise PersonalError('文件不存在。','not_found',404)
            return dict(row)

    def path(self, att):
        if not att.get('local_path'): raise PersonalError('文件尚未下载。','not_downloaded',409)
        path = Path(att['local_path']).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            raise PersonalError('原文件暂时无法读取。','file_unavailable',404)
        return path

    def preserve(self, temporary, ext):
        path = Path(temporary)
        size = path.stat().st_size
        if size > MAX_BYTES: raise DocumentError('file_too_large','文件超过 50 MB 上限，请拆分后收集。')
        with path.open('rb') as source: digest = hashlib.file_digest(source,'sha256').hexdigest()
        target = self.root / digest[:2] / (digest + '.' + (ext if ext.isalnum() and len(ext)<10 else 'bin'))
        target.parent.mkdir(exist_ok=True)
        if not target.exists():
            # Finish/sync before publishing a hard link; readers never see partial bytes.
            pending=target.with_name(identifier()+'.pending')
            try:
                with pending.open('xb') as out, path.open('rb') as source:
                    while chunk:=source.read(65536): out.write(chunk)
                    out.flush(); os.fsync(out.fileno())
                try: os.link(pending,target)
                except FileExistsError: pass
            finally: pending.unlink(missing_ok=True)
        return str(target.resolve()), digest, size

    def downloaded(self, att_id, temporary, mime, extension_override=None):
        old = self.get(att_id)
        ext = extension_override or old['extension']
        path, digest, size = self.preserve(temporary,ext)
        with self.personal.connection(write=True) as c:
            duplicate = c.execute("SELECT * FROM attachment WHERE raw_item_id=? AND original_url IS ? AND sha256=? AND parse_status='parsed' ORDER BY version DESC LIMIT 1",(old['raw_item_id'],old['original_url'],digest)).fetchone()
            if duplicate: return dict(duplicate)
            if old['parse_status']=='parsed':
                att_id = identifier()
                c.execute('''INSERT INTO attachment (id,raw_item_id,origin_snapshot_id,filename,original_url,extension,parse_status,created_at)
                    VALUES (?,?,?,?,?,?,'not_downloaded',?)''',(att_id,old['raw_item_id'],old['origin_snapshot_id'],old['filename'],old['original_url'],old['extension'],now()))
            version = c.execute('SELECT COALESCE(MAX(version),0)+1 FROM attachment WHERE raw_item_id=? AND original_url IS ? AND filename=?',(old['raw_item_id'],old['original_url'],old['filename'])).fetchone()[0]
            c.execute("UPDATE attachment SET local_path=?,sha256=?,size=?,mime_type=?,extension=?,downloaded_at=?,parse_status='downloaded',parse_error='',version=? WHERE id=?",(path,digest,size,mime,ext,now(),version,att_id))
            return dict(c.execute('SELECT * FROM attachment WHERE id=?',(att_id,)).fetchone())

    def collect(self, files, body, title, source_time=None, submission_id=None):
        if len(files)>10: raise PersonalError('每次最多收集 10 个文件。')
        saved = [(filename(name),mime,*self.preserve(path,extension(filename(name)))) for name,mime,path in files]
        fingerprint = hashlib.sha256(encode({'files':[(r[0],r[3]) for r in saved],'body':body,'title':title,'source_time':source_time}).encode()).hexdigest()
        with self.personal.connection(write=True) as c:
            if submission_id:
                old = c.execute('SELECT * FROM raw_item WHERE submission_id=?',(submission_id,)).fetchone()
                if old:
                    if old['submission_hash']!=fingerprint: raise PersonalError('保存请求内容已改变。','idempotency_conflict',409)
                    snap = c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=? ORDER BY created_at DESC LIMIT 1',(old['id'],)).fetchone()[0]
                    return ReviewStore._bundle(c,snap)
            raw,stamp = identifier(),now()
            c.execute("INSERT OR IGNORE INTO source VALUES ('manual_file','manual_file','文件与截图',?)",(stamp,))
            c.execute("INSERT INTO raw_item (id,source_id,kind,submission_id,submission_hash,created_at) VALUES (?,'manual_file','manual_file',?,?,?)",(raw,submission_id,fingerprint,stamp))
            snap = ReviewStore._new_snapshot(c,raw,body or '\n'.join(r[0] for r in saved),stamp,source_time,title)
            # Placeholder is explicitly marked: filenames cannot serve as extracted file evidence.
            c.execute('INSERT INTO snapshot_document VALUES (?,?,?,?,?,?,?,?)',(snap,snap,body,1,fingerprint,encode([]),encode([{'field_path':'sources','code':'files_pending','message':'文件已保存，等待本地解析。'}]),stamp))
            for name,mime,path,digest,size in saved:
                att = identifier()
                c.execute('''INSERT INTO attachment (id,raw_item_id,origin_snapshot_id,filename,local_path,mime_type,extension,size,sha256,downloaded_at,parse_status,version,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,'downloaded',1,?)''',(att,raw,snap,name,path,mime,extension(name),size,digest,stamp,stamp))
                c.execute('INSERT INTO snapshot_attachment VALUES (?,?)',(snap,att))
            return ReviewStore._bundle(c,snap)

    def compose(self, snapshot_id, parsed_ids):
        with self.personal.connection(write=True) as c:
            source = c.execute('SELECT * FROM raw_snapshot WHERE id=?',(snapshot_id,)).fetchone()
            previous = c.execute('SELECT * FROM snapshot_document WHERE raw_snapshot_id=?',(snapshot_id,)).fetchone()
            base = previous['body_snapshot_id'] if previous else snapshot_id
            body = previous['body_text'] if previous else source['original_text']
            original = c.execute('SELECT * FROM raw_snapshot WHERE id=?',(base,)).fetchone()
            title = c.execute('SELECT title_hint FROM raw_review WHERE raw_snapshot_id=?',(snapshot_id,)).fetchone()[0]
            prior = [r[0] for r in c.execute('SELECT attachment_id FROM snapshot_attachment WHERE raw_snapshot_id=?',(snapshot_id,))]
            chosen = {a['original_url'] or a['id']:a for a in [dict(c.execute('SELECT * FROM attachment WHERE id=?',(i,)).fetchone()) for i in [*prior,*parsed_ids]]}
            manifest = [{k:a[k] for k in ('id','sha256','filename','parse_status','extractor_version','version')} for a in chosen.values()]
            manifest_hash = hashlib.sha256(encode({'body':original['hash'],'attachments':manifest}).encode()).hexdigest()
            reused = c.execute('SELECT d.raw_snapshot_id FROM snapshot_document d JOIN raw_snapshot s ON s.id=d.raw_snapshot_id WHERE s.raw_item_id=? AND d.manifest_hash=?',(source['raw_item_id'],manifest_hash)).fetchone()
            if reused: return reused[0]
            blocks = []
            body_type = 'web' if c.execute('SELECT 1 FROM raw_snapshot_web WHERE raw_snapshot_id=?',(base,)).fetchone() else 'text'
            for i,b in enumerate(split_text(body,body_type,section='网页正文' if body_type=='web' else '粘贴原文'),1):
                blocks.append({**b,'block_id':f'{"WEB" if body_type=="web" else "TEXT"}_BODY_{i}','attachment_id':None,'filename':None})
            for att in chosen.values():
                if att['parse_status']!='parsed': continue
                for i,b in enumerate(json.loads(att['parse_manifest_json'])['blocks'],1):
                    blocks.append({**b,'block_id':f'ATTACHMENT_{att["id"]}_BLOCK_{i}','attachment_id':att['id'],'filename':att['filename']})
            warnings = document_warnings([{**b,'ordinal':i} for i,b in enumerate(blocks)])
            if any(a['parse_status']!='parsed' for a in chosen.values()): warnings.append({'field_path':'sources','code':'files_unparsed','message':'部分附件未解析，请核对未解析文件中的要求。'})
            if not blocks: raise PersonalError('未取得可分析文字；原文件仍保留。','files_unparsed',409)
            snap = ReviewStore._new_snapshot(c,source['raw_item_id'],'\n\n'.join(b['text'] for b in blocks),now(),source['source_time'],title)
            version = c.execute('SELECT COALESCE(MAX(d.version),0)+1 FROM snapshot_document d JOIN raw_snapshot s ON s.id=d.raw_snapshot_id WHERE s.raw_item_id=?',(source['raw_item_id'],)).fetchone()[0]
            c.execute('INSERT INTO snapshot_document VALUES (?,?,?,?,?,?,?,?)',(snap,base,body,version,manifest_hash,encode(manifest),encode(warnings),now()))
            for att in chosen.values(): c.execute('INSERT INTO snapshot_attachment VALUES (?,?)',(snap,att['id']))
            for i,b in enumerate(blocks):
                c.execute('''INSERT INTO raw_source_block (raw_snapshot_id,block_id,attachment_id,source_type,ordinal,text,page,section,bounding_box_json,words_json,raw_ocr_text,confidence,needs_review)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',(snap,b['block_id'],b['attachment_id'],b['source_type'],i,b['text'],b.get('page'),b.get('section'),encode(b['bounding_box']) if b.get('bounding_box') else None,encode(b.get('words',[])),b.get('raw_ocr_text'),b.get('confidence'),int(b.get('needs_review',False))))
            return snap
