"""Isolated photo-note regressions; never publishes fixture evidence or calls a model."""
import asyncio
import hashlib
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest

from server.documents.parsers import render_pdf
from server.cloud_sync.projection import CloudProjection
from server.documents.policy import DocumentError, MIMES, validate_file
from server.inbox_store import InboxStore
from server.learning.auto_notes import AutoNotes
from server.learning.service import LearningService
from server.school.models import LectureInput
from server.school.pdf_pages import catalog, parse_page
from server.test_school import rig


def photo(path):
    Image.new('RGB', (160, 100), 'white').save(path)
    return path


def collect_photo(r, path, *, linked=True):
    value = r.docs.files.collect([(path.name, MIMES[path.suffix[1:]], path)], '', path.stem, str(uuid4()))
    raw = value['snapshot']['raw_item_id']
    if linked: InboxStore(r.store).link(raw, 1, 'course-math-ii', '')
    return raw, value['snapshot']['id'], value['snapshot']['attachments'][0]


@pytest.mark.parametrize('ext', ['png', 'jpg', 'jpeg', 'webp'])
def test_original_photo_is_one_immutable_page_and_repeated_link_deduplicates(tmp_path, ext):
    async def run():
        async with rig(tmp_path) as r:
            path = photo(tmp_path / ('notes.' + ext))
            original = path.read_bytes()
            raw, _, attachment = collect_photo(r, path)
            doc = r.service.learning.register(attachment['id'])
            assert doc['id'] == 'doc-' + hashlib.sha256(original).hexdigest()[:32]
            assert doc['attachment_id'] == attachment['id'] and doc['page_count'] == 1
            assert doc['status'] == 'ready'
            page = r.service.learning.page(doc['id'], 1)
            assert page['printed_page_label'] is None and page['label_origin'] == 'unknown'
            assert raw in {row['raw_item_id'] for row in r.service.store.note_choices()}
            payload = LectureInput(title='Isolated photo lesson', lecture_date='2026-10-26', notes_raw_item_id=raw)
            key = str(uuid4())
            first = r.service.store.create_lecture(payload, key)
            second = r.service.store.create_lecture(payload, key)
            assert first['id'] == second['id']
            assert second['notes'][0]['id'] == doc['id']
            assert second['notes'][0]['mime_type'] == MIMES[ext]
            assert second['notes'][0]['extension'] == ext
            assert r.service.learning.get(doc['id'])['mime_type'] == MIMES[ext]
            source = r.service.contexts.image_source(doc, 1)
            assert source['caption'].endswith(' · 原图') and 'PDF' not in source['caption']
            r.service.learning.store_page(doc['id'],1,{'extracted_text':'Isolated OCR text remains unverified.','metadata':{}},method='windows.media.ocr',quality='ocr_unverified')
            block = r.service.learning.blocks([doc['id']])[0]
            text_source = r.service.contexts.block_source(block,'notes')
            assert text_source['caption'].endswith(' · 原图') and text_source['quality'] == 'ocr_unverified'
            assert r.docs.files.path(r.docs.files.get(attachment['id'])).read_bytes() == original
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM raw_item').fetchone()[0] == 1
                assert c.execute('SELECT count(*) FROM document').fetchone()[0] == 1
                assert c.execute('SELECT count(*) FROM lecture_note').fetchone()[0] == 1
                assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0] == 0
                assert c.execute('SELECT count(*) FROM review_artifact').fetchone()[0] == 0
    asyncio.run(run())


def test_photo_catalog_ocr_and_preview_preserve_origin_and_uncertainty(tmp_path, monkeypatch):
    path = photo(tmp_path / 'source.png')
    original = path.read_bytes()
    assert catalog(path) == {'page_count': 1, 'labels': {}, 'catalog_only': True, 'source_type': 'image'}
    def ocr(source, workdir):
        assert source == path
        return [{'text': 'Isolated unverified text', 'source_type': 'ocr', 'needs_review': True}], {'confidence': None}
    monkeypatch.setattr('server.school.pdf_pages.native_ocr', ocr)
    result = parse_page(path, 1, tmp_path)
    assert result['blocks'][0]['page'] == 1 and result['metadata']['quality'] == 'ocr_unverified'
    assert result['metadata']['needs_review'] and result['metadata']['printed_page_label'] is None
    for operation in (parse_page, render_pdf):
        with pytest.raises(DocumentError) as error: operation(path, 2, tmp_path / 'invalid.png')
        assert error.value.code == 'invalid_page'
    assert path.read_bytes() == original


def test_photo_preview_respects_exif_alpha_and_does_not_rewrite_original(tmp_path):
    path = tmp_path / 'rotated.jpg'
    image = Image.new('RGB', (120, 80), 'white')
    exif = image.getexif(); exif[274] = 6
    image.save(path, exif=exif)
    original = path.read_bytes()
    output = tmp_path / 'preview.png'
    render_pdf(path, 1, output)
    with Image.open(output) as preview: assert preview.size == (80, 120)
    assert path.read_bytes() == original
    transparent = tmp_path / 'transparent.png'
    Image.new('RGBA', (10, 10), (0, 0, 0, 0)).save(transparent)
    render_pdf(transparent, 1, output)
    with Image.open(output) as preview: assert preview.getpixel((0, 0)) == (255, 255, 255)


def test_animated_or_mislabeled_photo_is_preserved_but_not_silently_flattened(tmp_path):
    path = tmp_path / 'animated.webp'
    Image.new('RGB', (10, 10), 'white').save(path, save_all=True, append_images=[Image.new('RGB', (10, 10), 'black')], duration=50)
    original = path.read_bytes()
    with pytest.raises(DocumentError) as error: validate_file(path, 'webp')
    assert error.value.code == 'animated_image' and path.read_bytes() == original
    with pytest.raises(DocumentError) as error: validate_file(path, 'png')
    assert error.value.code == 'mime_mismatch'


@pytest.mark.parametrize('status', ['needs_source', 'local_ready', 'needs_vision_permission'])
def test_no_lecture_preserves_real_note_state_without_attempting_review(status):
    def forbidden(*args): raise AssertionError('No lecture: must not inspect it or start analysis')
    service = SimpleNamespace(personal=None, school=SimpleNamespace(store=SimpleNamespace(lecture=forbidden)), analysis_status=forbidden)
    context = {'lecture_id': None, 'note_automation': {'status': status, 'message': 'Original safely saved'}}
    asyncio.run(AutoNotes(service).review(context))
    assert context['note_automation'] == {'status': status, 'message': 'Original safely saved'}


def test_unknown_photo_course_remains_actionable_instead_of_bogus_missing_lecture(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            raw, _, _ = collect_photo(r, photo(tmp_path / 'meeting.png'), linked=False)
            learning = LearningService(r.store, r.service, r.extract)
            context = {'course_id': None, 'lecture_id': None}
            notes = AutoNotes(learning)
            await notes.run(raw, 'A meeting', 'Unrelated meeting agenda.', context)
            await notes.review(context)
            assert context['note_automation']['status'] == 'needs_context'
            assert '课程' in context['note_automation']['message']
            with r.store.connection() as c: assert c.execute('SELECT count(*) FROM lecture').fetchone()[0] == 0
            assert not r.provider.calls
            await learning.close()
    asyncio.run(run())


def test_text_only_classroom_input_keeps_original_source_needed_state(tmp_path):
    async def run():
        from server.review_store import ReviewStore
        from server.extraction_models import RawInput
        async with rig(tmp_path) as r:
            value = ReviewStore(r.store).collect(RawInput(original_text='Synthetic text-only note', title_hint='Classroom note'), str(uuid4()))
            learning = LearningService(r.store, r.service, r.extract)
            context = {'course_id': None, 'lecture_id': None}
            notes = AutoNotes(learning)
            await notes.run(value['snapshot']['raw_item_id'], 'Classroom note', 'Synthetic text-only note', context)
            await notes.review(context)
            assert context['note_automation']['status'] == 'needs_source'
            assert not r.provider.calls
            await learning.close()
    asyncio.run(run())


def test_existing_photo_lecture_reuses_index_and_never_invents_attempts(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            raw, _, attachment = collect_photo(r, photo(tmp_path / 'notes.png'))
            doc = r.service.learning.register(attachment['id'])
            r.service.learning.store_page(doc['id'], 1, {'extracted_text': 'Synthetic OCR only.', 'metadata': {}}, method='windows.media.ocr', quality='ocr_unverified')
            lesson = r.service.store.create_lecture(LectureInput(title='Photo lesson', lecture_date='2026-10-26', notes_raw_item_id=raw), str(uuid4()))
            learning = LearningService(r.store, r.service, r.extract)
            for _ in range(2):
                context = {'course_id': 'course-math-ii', 'source_time': '2026-10-26T10:00:00+08:00'}
                await AutoNotes(learning).run(raw, 'Synthetic photo note', 'Synthetic OCR only.', context)
                assert context['lecture_id'] == lesson['id']
                assert context['note_automation']['status'] == 'needs_source'
                assert context['note_automation']['document_id'] == doc['id']
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM lecture').fetchone()[0] == 1
                assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0] == 0
                assert c.execute('SELECT extraction_quality FROM document_block').fetchone()[0] == 'ocr_unverified'
            assert not r.provider.calls
            await learning.close()
    asyncio.run(run())


def test_automatic_recovery_selects_only_known_missing_lecture_failure(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            learning = LearningService(r.store, r.service, r.extract)
            rows = [
                {'note_share': True, 'note_automation': {'status': 'needs_review', 'message': '课次不存在。'}},
                {'note_share': True, 'lecture_id': 'deleted-real-lecture', 'note_automation': {'status': 'needs_review', 'message': '课次不存在。'}},
                {'note_share': True, 'note_automation': {'status': 'needs_review', 'message': 'User must confirm'}},
                {'note_share': False, 'note_automation': {'status': 'needs_review', 'message': '课次不存在。'}},
                {'note_share': True, 'note_automation': {'status': 'needs_context', 'message': 'Uncertain course'}},
            ]
            ids = []
            for index, context in enumerate(rows):
                raw, sid, _ = collect_photo(r, photo(tmp_path / f'case-{index}.png'))
                ids.append(learning.store.record_capture(raw, sid, str(index), context)['id'])
            selected = []
            learning.schedule = lambda key, work: selected.append(key)
            learning.resume_background()
            assert selected == ['note:' + ids[0]]
            await learning.close()
    asyncio.run(run())


def test_cloud_photo_document_points_to_same_private_original_without_pdf_copy(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            path = photo(tmp_path / 'source.png')
            raw, _, attachment = collect_photo(r, path)
            doc = r.service.learning.register(attachment['id'])
            projection = CloudProjection(r.store, tmp_path / 'cloud-cache')
            resources, documents = projection.resources()
            assert len(resources) == 1 and len(documents) == 1
            assert resources[0]['id'] == 'attachment:' + attachment['id'] + ':original'
            assert resources[0]['mime'] == 'image/png' and resources[0]['path'].read_bytes() == path.read_bytes()
            value = next(row['payload'] for row in projection.records() if row['kind'] == 'document')
            assert value['id'] == doc['id'] and value['attachment_id'] == attachment['id']
            assert value['mime_type'] == 'image/png' and 'reading_resource_id' not in value
            assert not projection.reading_views
    asyncio.run(run())
