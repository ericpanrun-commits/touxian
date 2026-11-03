import asyncio,hashlib,json,os
from pathlib import Path
import pytest
from server.personal_store import PersonalError
from server.school import library
from server.school.library import register_canonical
from server.test_school import rig
from server.test_documents import text_pdf
from server.school.models import PageAuditInput
from server.school.api import audit_transcription


def fixture_library(root):
    root.mkdir();parts=['# fixture manifest']
    for volume,name in [('上册','upper.pdf'),('下册','lower.pdf')]:
        path=text_pdf(root/name,[volume.encode('unicode_escape').decode()+' fixture actual PDF original text layer.'])
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        parts.append(f'## {volume}\n- 文件名：{name}\n- 路径：{path}\n- 页数：1\n- 大小：{path.stat().st_size:,} 字节\n- SHA-256：{sha}\n- 已确认元数据：test fixture\n- 未确认元数据：fixture not a formal textbook\n')
    (root/'manifest.md').write_text('\n'.join(parts),encoding='utf-8')
    return root


def test_canonical_import_no_copy_idempotency_and_companion(tmp_path,monkeypatch):
    books=fixture_library(tmp_path/'books');monkeypatch.setattr(library,'LIBRARY_ROOT',books)
    async def run():
        async with rig(tmp_path/'runtime') as r:
            first=register_canonical(r.service);second=register_canonical(r.service)
            assert len(first['books'])==2 and first['copied_bytes']==0 and all(b['reused'] for b in second['books'])
            book=r.service.store.book();assert book['document']['page_count']==1 and book['companions'][0]['document']['page_count']==1
            assert not list(r.docs.files.root.rglob('*.pdf'))
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM attachment').fetchone()[0]==2
                for row in c.execute('SELECT * FROM attachment'):
                    att=dict(row);assert r.docs.files.path(att).is_relative_to(books)
                assert not r.service.store.note_choices()
    asyncio.run(run())


@pytest.mark.parametrize('change',['hash','path','page_count','bytes'])
def test_manifest_mismatch_rejects_before_any_mutation(tmp_path,monkeypatch,change):
    books=fixture_library(tmp_path/'books');monkeypatch.setattr(library,'LIBRARY_ROOT',books)
    path=books/'manifest.md';text=path.read_text(encoding='utf-8')
    if change=='hash':text=text.replace(hashlib.sha256((books/'upper.pdf').read_bytes()).hexdigest(),'0'*64)
    if change=='path':text=text.replace(str(books/'upper.pdf'),str(tmp_path/'upper.pdf'))
    if change=='page_count':text=text.replace('页数：1','页数：2')
    if change=='bytes':text=text.replace('大小：','大小：9')
    path.write_text(text,encoding='utf-8')
    async def run():
        async with rig(tmp_path/'runtime') as r:
            with pytest.raises(PersonalError):register_canonical(r.service)
            with r.store.connection() as c:assert c.execute('SELECT count(*) FROM attachment').fetchone()[0]==0
    asyncio.run(run())


def test_changed_registered_original_and_unregistered_path_denied(tmp_path,monkeypatch):
    books=fixture_library(tmp_path/'books');monkeypatch.setattr(library,'LIBRARY_ROOT',books)
    async def run():
        async with rig(tmp_path/'runtime') as r:
            registered=register_canonical(r.service)
            att=r.service.learning.attachment(registered['books'][0]['document_id'])
            path=r.docs.files.path(att)
            os.utime(path,ns=(path.stat().st_atime_ns,path.stat().st_mtime_ns+1000000000))
            with pytest.raises(PersonalError) as error:r.docs.files.path(att)
            assert error.value.code=='original_changed'
            with pytest.raises(PersonalError):r.docs.files.path({**att,'local_path':str(books/'lower.pdf')})
            outside=text_pdf(tmp_path/'outside.pdf',['outside arbitrary file'])
            with pytest.raises(PersonalError):r.docs.files.path({**att,'local_path':str(outside)})
    asyncio.run(run())


def test_audited_transcription_append_only_source_and_conflict(tmp_path,monkeypatch):
    books=fixture_library(tmp_path/'books');monkeypatch.setattr(library,'LIBRARY_ROOT',books)
    async def run():
        async with rig(tmp_path/'runtime') as r:
            doc=register_canonical(r.service)['books'][0]['document_id']
            r.service.learning.store_page(doc,1,{'extracted_text':'原 OCR 内容不可靠，需要对照原页。','metadata':{}},method='windows.media.ocr',quality='ocr_unverified')
            page=r.service.learning.page(doc,1);document=r.service.learning.get(doc)
            payload=PageAuditInput(expected_version=page['version'],document_sha256=document['sha256'],paragraphs=['定义：这是经过逐页对照的测试转写。'],printed_page_label='1',audit_render_sha256='a'*64,reviewer='codex_visual_audit')
            audit_transcription(doc,1,payload,r.service)
            current=r.service.learning.blocks([doc]);history=r.service.learning.blocks([doc],current=False)
            assert current[0]['extraction_quality']=='visually_checked'
            assert any(b['extraction_quality']=='ocr_unverified' for b in history)
            assert r.service.learning.page(doc,1)['label_origin']=='visually_checked'
            with pytest.raises(PersonalError):audit_transcription(doc,1,payload,r.service)
            fresh=r.service.learning.page(doc,1)
            changed=payload.model_copy(update={'expected_version':fresh['version'],'uncertain_paragraphs':[0],'uncertainties':['原页数学内容仍需核对。']})
            audit_transcription(doc,1,changed,r.service)
            assert r.service.learning.blocks([doc])[0]['extraction_quality']=='vision_uncertain'
            assert r.service.learning.page(doc,1)['vision_status']=='uncertain'
            assert len(r.service.learning.blocks([doc],current=False))==3
    asyncio.run(run())


def test_real_ocr_fragment_and_exact_math_ranking(tmp_path):
    from server.test_school import prepared
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path);doc=r.service.store.book()['document_id']
            r.service.learning.store_page(doc,1,{'extracted_text':'夹逼定理得 lim 叾。','metadata':{}},method='windows.media.ocr',quality='ocr_unverified')
            r.service.learning.store_page(doc,2,{'extracted_text':'定理1（夹逼定理）：两个边界有相同极限且夹住目标函数。','metadata':{}},method='visual_audit_transcription',quality='visually_checked')
            result=r.service.retrieval.search('夹逼定理的条件',lecture_id=p.lecture['id'])
            assert result['items'][0]['page_index']==2
            r.service.learning.store_page(doc,3,{'extracted_text':'第二个重要极限：lim (1+1/x)^x=e。','metadata':{}},method='visual_audit_transcription',quality='visually_checked')
            r.service.learning.store_page(doc,4,{'extracted_text':'例8：使用第二个重要极限，求 (1+5/x)^x。','metadata':{}},method='visual_audit_transcription',quality='visually_checked')
            found=r.service.retrieval.search('第二个重要极限 (1+1/x)^x',lecture_id=p.lecture['id'])
            assert found['items'][0]['page_index']==3
    asyncio.run(run())
