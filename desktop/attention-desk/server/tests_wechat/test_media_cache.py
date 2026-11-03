"""Message-bound local files and image decoding, entirely synthetic bytes."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from server.wechat_collect.db_media import cache_references,describe_content,preserve_local_media

ROOM='synthetic@chatroom'


@pytest.fixture(autouse=True)
def owned_output_root(tmp_path,monkeypatch):
    from server.wechat_collect import db_reader
    monkeypatch.setattr(db_reader,'PROJECT_ROOT',tmp_path)


@pytest.mark.parametrize('name',['活动说明.pdf','学习方案.docx','学生承诺书.doc','统计表.xlsx','成绩表.xls'])
def test_named_cache_file_requires_message_size_and_md5(tmp_path,name):
    folder=tmp_path/'msg/file/2026-10';folder.mkdir(parents=True)
    path=folder/name;path.write_bytes(b'correct synthetic file')
    other=tmp_path/'msg/file/2026-09';other.mkdir();(other/name).write_bytes(b'incorrect synthetic!!')
    digest=hashlib.md5(path.read_bytes()).hexdigest()
    xml=f'<msg><appmsg><type>6</type><title>{name}</title><md5>{digest}</md5><appattach><totallen>{path.stat().st_size}</totallen><aeskey>never-expose</aeskey></appattach></appmsg></msg>'
    raw={'local_type':49,'local_id':1,'shard':'one','type':'文件'}
    text,ref=describe_content(xml,raw)
    reader=SimpleNamespace(account_dir=str(tmp_path),allowed_ids={ROOM},get_message_row=lambda *a,**k:{'content':xml,'packed_info':b''})
    message={**raw,'media_reference':ref}
    matches=cache_references(reader,ROOM,message)
    assert [Path(r['path']) for r in matches]==[path.resolve()]
    assert ref['binding']=='message_size_and_md5' and 'never-expose' not in str(ref)
    output=tmp_path/'artifacts/owned'
    saved=preserve_local_media(reader,ROOM,message,output)
    assert Path(saved).read_bytes()==path.read_bytes()
    assert ref['evidence_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    ref['file_md5']='0'*32
    assert cache_references(reader,ROOM,message)==[]


def test_unbound_filename_and_outside_account_paths_are_not_evidence(tmp_path):
    outer=tmp_path/'other-account';outer.mkdir();file=outer/'document.pdf';file.write_bytes(b'outside')
    account=tmp_path/'account';account.mkdir()
    xml=f'<msg><appmsg><filepath>{file}</filepath></appmsg></msg>'
    reader=SimpleNamespace(account_dir=str(account),allowed_ids={ROOM},get_message_row=lambda *a,**k:{'content':xml,'packed_info':b''})
    message={'local_id':1,'shard':'one','media_reference':{'kind':'file','title':'document.pdf'}}
    assert cache_references(reader,ROOM,message)==[]
    with pytest.raises(RuntimeError,match='not_allowlisted'):cache_references(reader,'unauthorized@chatroom',message)


@pytest.mark.parametrize('format',['PNG','JPEG','GIF','WEBP'])
def test_image_multiple_message_hashes_and_unreadable_original_fall_back_to_thumbnail(tmp_path,format):
    md5='a'*32;packed_digest='b'*32
    folder=tmp_path/'msg/attach'/hashlib.md5(ROOM.encode()).hexdigest()/'2026-10/Img';folder.mkdir(parents=True)
    original=folder/(packed_digest+'_h.dat');original.write_bytes(b'bad')
    image=tmp_path/'image';Image.new('RGB',(30,30),'white').save(image,format=format)
    plain=image.read_bytes();encrypted=bytes(b^0x37 for b in plain)
    (folder/(md5+'_t.dat')).write_bytes(encrypted)
    reader=SimpleNamespace(account_dir=str(tmp_path),allowed_ids={ROOM},get_message_row=lambda *a,**k:{'packed_info':packed_digest.encode()+b' '+b'c'*32})
    ref={'kind':'image','md5':md5}
    saved=preserve_local_media(reader,ROOM,{'local_id':1,'shard':'one','media_reference':ref},tmp_path/'artifacts/owned')
    assert Path(saved).read_bytes()==plain and ref['evidence_tier']=='thumb'
    assert Path(ref['encrypted_evidence_path']).read_bytes()==encrypted
