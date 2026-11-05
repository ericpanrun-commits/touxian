"""Small append-only PDF reading views over unchanged, immutable originals.

Some scanned books have a flat page tree. PDF.js then fetches hundreds of page
dictionaries from distant parts of the file before showing one page. A standard
incremental update groups page dictionaries in a small tail; all original bytes,
content streams, page order and geometry remain unchanged.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import re

from pypdf import PdfReader
from pypdf.generic import ArrayObject,DictionaryObject,IndirectObject,NameObject,NumberObject

VERSION='pdf-reading-tree-1'
MAX_TAIL=1_000_000


class ReadingStream(io.RawIOBase):
    """Read/seek a file plus a small tail without copying the original."""
    def __init__(self,path,tail):
        self.source=open(path,'rb');self.base=Path(path).stat().st_size;self.tail=tail;self.position=0
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=0):
        value=offset if whence==0 else self.position+offset if whence==1 else self.base+len(self.tail)+offset
        if value<0:raise ValueError('Negative PDF seek')
        self.position=value;return value
    def read(self,size=-1):
        end=self.base+len(self.tail) if size<0 else min(self.position+size,self.base+len(self.tail));chunks=[]
        if self.position<self.base and end>self.position:
            self.source.seek(self.position);chunk=self.source.read(min(end,self.base)-self.position);chunks.append(chunk);self.position+=len(chunk)
        if self.position>=self.base and end>self.position:
            chunk=self.tail[self.position-self.base:end-self.base];chunks.append(chunk);self.position+=len(chunk)
        return b''.join(chunks)
    def readinto(self,buffer):
        data=self.read(len(buffer));buffer[:len(data)]=data;return len(data)
    def close(self):
        if not self.closed:self.source.close()
        super().close()


def _tail(path):
    size=path.stat().st_size
    with path.open('rb') as stream:
        reader=PdfReader(stream)
        if reader.is_encrypted or not 64<=len(reader.pages)<=5000:return None
        pages=list(reader.pages);references=[page.indirect_reference for page in pages]
        if any(ref is None for ref in references) or len({(ref.idnum,ref.generation) for ref in references})!=len(pages):return None
        root=reader.root_object.raw_get('/Pages')
        if not isinstance(root,IndirectObject):return None
        stream.seek(max(0,size-1_000_000));matches=list(re.finditer(rb'startxref\s+(\d+)\s+%%EOF',stream.read()))
        if not matches:raise ValueError('Original PDF has no final xref')
        previous=int(matches[-1][1]);next_id=int(reader.trailer['/Size']);updates={}
        groups=[]
        for start in range(0,len(pages),32):
            group=IndirectObject(next_id,0,reader);next_id+=1;groups.append(group)
            kids=references[start:start+32]
            updates[(group.idnum,0)]=DictionaryObject({NameObject('/Type'):NameObject('/Pages'),NameObject('/Parent'):root,NameObject('/Count'):NumberObject(len(kids)),NameObject('/Kids'):ArrayObject(kids)})
            for page,reference in zip(pages[start:start+32],kids):
                value=DictionaryObject(dict(page));value[NameObject('/Parent')]=group;updates[(reference.idnum,reference.generation)]=value
        top=DictionaryObject(dict(root.get_object()));top[NameObject('/Kids')]=ArrayObject(groups);top[NameObject('/Count')]=NumberObject(len(pages));updates[(root.idnum,root.generation)]=top
        tail=io.BytesIO();tail.write(b'\n');offsets=[]
        for (number,generation),value in sorted(updates.items()):
            offsets.append((number,generation,size+tail.tell()));tail.write(f'{number} {generation} obj\n'.encode());value.write_to_stream(tail);tail.write(b'\nendobj\n')
        xref=size+tail.tell();tail.write(b'xref\n')
        for number,generation,offset in offsets:tail.write(f'{number} 1\n{offset:010d} {generation:05d} n \n'.encode())
        trailer=DictionaryObject({NameObject('/Size'):NumberObject(next_id),NameObject('/Prev'):NumberObject(previous)})
        for key in ('/Root','/Info','/ID'):
            if key in reader.trailer:trailer[NameObject(key)]=reader.trailer.raw_get(key)
        tail.write(b'trailer\n');trailer.write_to_stream(tail);tail.write(f'\nstartxref\n{xref}\n%%EOF\n'.encode());data=tail.getvalue()
        if len(data)>MAX_TAIL:raise ValueError('Reading index exceeds its metadata limit')
        expected=[(ref.idnum,ref.generation) for ref in references]
    with ReadingStream(path,data) as combined:
        checked=PdfReader(combined,strict=True)
        if [(page.indirect_reference.idnum,page.indirect_reference.generation) for page in checked.pages]!=expected:raise ValueError('Reading index changed page order')
    return data,len(pages)


def reading_view(document,cache):
    path=Path(document['path']);before=path.stat();source_hash=document['sha256']
    Path(cache).mkdir(parents=True,exist_ok=True)
    target=Path(cache)/(VERSION+'-'+source_hash+'.json')
    if target.exists():
        try:
            value=json.loads(target.read_text(encoding='utf-8'))
            tail=base64.b64decode(value['tail_base64'],validate=True)
            if (value.pop('_source_mtime_ns',None)==before.st_mtime_ns and value['source_sha256']==source_hash and
                value['source_bytes']==before.st_size and value['page_count']==document['page_count'] and
                value['document_id']==document['id'] and value['source_resource_id']=='attachment:'+document['attachment_id']+':original' and
                0<len(tail)==value['tail_bytes']<=MAX_TAIL and hashlib.sha256(tail).hexdigest()==value['tail_sha256'] and
                value['id']==document['id']+':'+value['tail_sha256'] and value['bytes']==before.st_size+len(tail)):
                return value
        except (ValueError,KeyError,TypeError):pass
    generated=_tail(path)
    if generated is None:return None
    tail,page_count=generated;original=hashlib.sha256();combined=hashlib.sha256()
    if page_count!=document['page_count']:raise ValueError('Original PDF page count differs from its document')
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(4_000_000),b''):original.update(chunk);combined.update(chunk)
    combined.update(tail);after=path.stat()
    if original.hexdigest()!=source_hash or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Original changed during reading-index generation')
    tail_hash=hashlib.sha256(tail).hexdigest();identity=document['id']+':'+tail_hash
    value={'id':identity,'document_id':document['id'],'version':VERSION,'source_resource_id':'attachment:'+document['attachment_id']+':original',
           'source_sha256':source_hash,'source_bytes':before.st_size,'page_count':page_count,'tail_sha256':tail_hash,'tail_bytes':len(tail),
           'tail_base64':base64.b64encode(tail).decode(),'bytes':before.st_size+len(tail),'sha256':combined.hexdigest()}
    temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps({**value,'_source_mtime_ns':after.st_mtime_ns}),encoding='utf-8');temporary.replace(target)
    return value
