"""Register verified Eric OS library originals without copying PDF bytes."""
import hashlib
import json
import re
from pathlib import Path
from pypdf import PdfReader
from ..personal_store import PersonalError, encode, identifier, now
from ..extraction_models import RawInput
from ..review_store import ReviewStore
from .models import COURSE, TEXTBOOK

LIBRARY_ROOT = Path(r'D:\Eric OS\data\textbooks\高等数学')
LOWER = 'textbook-huang-v2-lower'


def registered_path(personal, attachment, path):
    """Only a verified registry entry may resolve outside the existing CAS root."""
    root=LIBRARY_ROOT.resolve()
    if not path.is_relative_to(root) or path.suffix.lower()!='.pdf' or not path.is_file():
        raise PersonalError('原文件暂时无法读取。','file_unavailable',404)
    with personal.connection() as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='textbook_original'").fetchone()
        row=c.execute('SELECT * FROM textbook_original WHERE attachment_id=?',(attachment['id'],)).fetchone() if exists else None
    if not row or str(path)!=row['canonical_path'] or attachment['sha256']!=row['sha256']:
        raise PersonalError('原件没有正式教材登记，不能读取此路径。','file_unavailable',404)
    stat=path.stat()
    if stat.st_size!=row['bytes'] or stat.st_mtime_ns!=row['mtime_ns']:
        raise PersonalError('教材原件发生变化，请先重新核验；旧引用未自动改指向。','original_changed',409)
    return path


def inspect_manifest():
    root=LIBRARY_ROOT.resolve();manifest=root/'manifest.md'
    if not manifest.is_file():raise PersonalError('正式教材目录缺少 manifest.md。','manifest_missing',409)
    content=manifest.read_bytes();text=content.decode('utf-8-sig')
    results=[]
    for volume,title,book_id in [('上册','高等数学（第二版）（上）',TEXTBOOK),('下册','高等数学（第二版）（下）',LOWER)]:
        match=re.search(r'## '+volume+r'\s*(.*?)(?=\n## |\Z)',text,re.S)
        if not match:raise PersonalError('教材 manifest 缺少上下册记录。','manifest_invalid',400)
        part=match.group(1)
        def field(pattern):
            value=re.search(pattern,part)
            if not value:raise PersonalError('教材 manifest 字段不完整。','manifest_invalid',400)
            return value.group(1).strip()
        name=field(r'- 文件名：([^\r\n]+)');path=(root/name).resolve()
        declared=Path(field(r'- 路径：([^\r\n]+)')).resolve()
        if path!=declared or not path.is_relative_to(root) or path.suffix.lower()!='.pdf' or not path.is_file():
            raise PersonalError('教材路径不属于指定正式目录。','library_scope',400)
        expected_hash=field(r'- SHA-256：([a-fA-F0-9]{64})').lower()
        expected_size=int(field(r'- 大小：([\d,]+)').replace(',',''));pages=int(field(r'- 页数：(\d+)'))
        before=path.stat()
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or after.st_size!=expected_size or digest!=expected_hash:
            raise PersonalError('教材大小或 SHA-256 与 manifest 不一致。','manifest_mismatch',409)
        reader=PdfReader(path,strict=True)
        if reader.is_encrypted or len(reader.pages)!=pages or not 1<=pages<=2000:
            raise PersonalError('教材 PDF 页数或结构与 manifest 不一致。','manifest_mismatch',409)
        for page in reader.pages:
            if float(page.mediabox.width)<=0 or float(page.mediabox.height)<=0:
                raise PersonalError('教材存在不可读取的页面结构。','corrupt_document',409)
        metadata={'title':title,'volume':volume,'confirmed_metadata':field(r'- 已确认元数据：([^\r\n]+)'),
                  'unconfirmed_metadata':field(r'- 未确认元数据：([^\r\n]+)'),
                  'verified_by':'manifest hash + PDF page structure','source':'Eric OS canonical textbook library'}
        results.append({'id':book_id,'path':path,'sha256':digest,'bytes':after.st_size,'mtime_ns':after.st_mtime_ns,
                        'page_count':pages,'manifest_sha256':hashlib.sha256(content).hexdigest(),'metadata':metadata})
    return results


def register_canonical(service):
    inspected=inspect_manifest()  # Validate both volumes before any database mutation.
    registered=[]
    for item in inspected:
        with service.personal.connection(write=True) as c:
            existing=c.execute('SELECT * FROM textbook_original WHERE sha256=?',(item['sha256'],)).fetchone()
            if existing:
                if existing['canonical_path']!=str(item['path']) or existing['bytes']!=item['bytes'] or existing['mtime_ns']!=item['mtime_ns']:
                    raise PersonalError('已有原件登记与当前文件不同，请保留旧版本并核验。','original_changed',409)
                aid=existing['attachment_id']
            else:
                same=c.execute('SELECT * FROM attachment WHERE sha256=? ORDER BY created_at LIMIT 1',(item['sha256'],)).fetchone()
                if same:
                    aid=same['id']
                    c.execute('UPDATE attachment SET local_path=? WHERE id=?',(str(item['path']),aid))
                else:
                    body=f"正式教材原件登记：{item['metadata']['title']}。共 {item['page_count']} 个 PDF 页；此登记文字不是教材正文。"
                    bundle=ReviewStore(service.personal).collect(RawInput(original_text=body,title_hint=item['metadata']['title']),
                         'canonical-textbook-'+item['sha256'],source_id='school_library',source_kind='school_library',
                         source_name='Eric OS 正式教材库',raw_kind='inbox_textbook',connection=c)
                    raw,snap=bundle['snapshot']['raw_item_id'],bundle['snapshot']['id'];aid=identifier();stamp=now()
                    c.execute('''INSERT INTO attachment(id,raw_item_id,origin_snapshot_id,filename,local_path,mime_type,extension,size,sha256,downloaded_at,parse_status,version,created_at)
                        VALUES (?,?,?,?,?,'application/pdf','pdf',?,?,?,'downloaded',1,?)''',
                        (aid,raw,snap,item['path'].name,str(item['path']),item['bytes'],item['sha256'],stamp,stamp))
                    c.execute('INSERT INTO snapshot_attachment VALUES (?,?)',(snap,aid))
                    c.execute("UPDATE raw_item SET inbox_course_id=?,inbox_kind='pdf',inbox_status='needs_review',inbox_stage='textbook_registered',inbox_original_snapshot_id=?,inbox_snapshot_id=?,inbox_updated_at=? WHERE id=?",
                              (COURSE,snap,snap,stamp,raw))
                c.execute('INSERT INTO textbook_original VALUES (?,?,?,?,?,?,?,?,?)',
                          (aid,str(item['path']),item['sha256'],item['bytes'],item['mtime_ns'],item['page_count'],item['manifest_sha256'],encode(item['metadata']),now()))
        doc=service.learning.register(aid)
        service.learning.catalog(doc['id'],{'page_count':item['page_count'],'labels':{}})
        with service.personal.connection(write=True) as c:
            current=c.execute('SELECT * FROM textbook WHERE id=?',(item['id'],)).fetchone()
            if current['document_id']!=doc['id']:
                c.execute("UPDATE textbook SET document_id=?,file_role='full_textbook',status='ready',is_fixture=0,acceptance_status='pending_evaluation',acceptance_json=NULL,accepted_at=NULL,version=version+1,updated_at=? WHERE id=?",
                          (doc['id'],now(),item['id']))
        registered.append({'textbook_id':item['id'],'document_id':doc['id'],'sha256':item['sha256'],'bytes':item['bytes'],
                           'pages':item['page_count'],'reused':bool(existing),'copied_bytes':0})
    return {'books':registered,'copied_bytes':0,'source':'Eric OS canonical textbook library'}
