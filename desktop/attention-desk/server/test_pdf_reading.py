"""Reading views preserve immutable content and original page/evidence identities."""
import base64
import hashlib
import io
import json

import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader,PdfWriter
from pypdf.generic import NameObject,NumberObject,RectangleObject

from server.cloud_sync.pdf_reading import ReadingStream,reading_view
from server.test_documents import text_pdf


def fixture(tmp_path):
    path=text_pdf(tmp_path/'book.pdf',['Synthetic page '+str(i) for i in range(70)])
    reader=PdfReader(path);writer=PdfWriter(clone_from=reader)
    for i,page in enumerate(writer.pages):
        if i%3==0:page[NameObject('/Rotate')]=NumberObject(90)
        if i%4==0:page[NameObject('/CropBox')]=RectangleObject([20,30,580,770])
    # Exercise PDF inheritance, rather than only dictionaries made by this app.
    root=writer._root_object['/Pages']
    root[NameObject('/MediaBox')]=writer.pages[0].raw_get('/MediaBox')
    for page in writer.pages:del page['/MediaBox']
    writer.write(path)
    document={'path':path,'id':'doc-stable','attachment_id':'attachment-stable','page_count':70,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    return document


def test_incremental_index_preserves_all_page_ids_streams_and_geometry(tmp_path):
    document=fixture(tmp_path);path=document['path'];before=path.read_bytes()
    view=reading_view(document,tmp_path/'cache');tail=base64.b64decode(view['tail_base64'])
    original=PdfReader(io.BytesIO(before))
    with ReadingStream(path,tail) as stream:
        indexed=PdfReader(stream,strict=True)
        assert len(indexed.pages)==len(original.pages)==70
        assert len(indexed.root_object['/Pages']['/Kids'])==3
        for old,new in zip(original.pages,indexed.pages):
            assert (old.indirect_reference.idnum,old.indirect_reference.generation)==(new.indirect_reference.idnum,new.indirect_reference.generation)
            assert old.get_contents().get_data()==new.get_contents().get_data()
            assert old.mediabox==new.mediabox and old.cropbox==new.cropbox and old.rotation==new.rotation
        stream.seek(len(before)-10)
        assert stream.read(20)==(before+tail)[len(before)-10:len(before)+10]
        stream.seek(-10,2);buffer=bytearray(10);assert stream.readinto(buffer)==10;assert bytes(buffer)==tail[-10:]
    assert path.read_bytes()==before
    assert view['source_sha256']==hashlib.sha256(before).hexdigest()
    assert view['sha256']==hashlib.sha256(before+tail).hexdigest()
    assert view['tail_bytes']==len(tail)<1_000_000
    assert reading_view(document,tmp_path/'cache')==view
    assert not any(key.startswith('_') for key in view)


def test_incremental_view_renders_identical_rotated_cropped_and_inherited_pages(tmp_path):
    document=fixture(tmp_path);tail=base64.b64decode(reading_view(document,tmp_path/'cache')['tail_base64'])
    with ReadingStream(document['path'],tail) as stream:
        original=pdfium.PdfDocument(document['path']);indexed=pdfium.PdfDocument(stream)
        try:
            for index in (0,31,32,63,64,69):
                pages=[original[index],indexed[index]];bitmaps=[]
                try:
                    bitmaps=[page.render(scale=.5) for page in pages]
                    assert bitmaps[0].to_pil().tobytes()==bitmaps[1].to_pil().tobytes()
                finally:
                    for bitmap in bitmaps:bitmap.close()
                    for page in pages:page.close()
        finally:original.close();indexed.close()


def test_corrupt_cache_is_rebuilt_and_changed_original_is_rejected(tmp_path):
    document=fixture(tmp_path);cache=tmp_path/'cache';view=reading_view(document,cache)
    cached=next(cache.glob('*.json'));value=json.loads(cached.read_text());value['tail_base64']='bad tail';cached.write_text(json.dumps(value))
    assert reading_view(document,cache)==view
    document['path'].write_bytes(document['path'].read_bytes()+b'\nchanged')
    with pytest.raises(ValueError,match='Original changed'):reading_view(document,cache)


def test_small_books_keep_original_and_metadata_mismatch_is_rejected(tmp_path):
    path=text_pdf(tmp_path/'small.pdf',['Synthetic short note.'])
    document={'path':path,'id':'small','attachment_id':'small','page_count':1,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    assert reading_view(document,tmp_path/'cache') is None
    document=fixture(tmp_path);document['page_count']=71
    with pytest.raises(ValueError,match='page count'):reading_view(document,tmp_path/'cache')
