"""Selected-page parsing in the existing isolated parser process."""
import re
from pathlib import Path
from pypdf import PdfReader
from ..documents.policy import DocumentError, validate_file, MAX_TEXT, IMAGE_EXTENSIONS
from ..documents.parsers import render_pdf, native_ocr, split_text


def reader_for(path):
    validate_file(path, 'pdf')
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted: raise DocumentError('encrypted_pdf', 'PDF 已保存；请使用可读取的非加密副本。')
        if not 1 <= len(reader.pages) <= 2000: raise DocumentError('page_limit', '学习文档支持 1–2000 页；原件已保存。')
        return reader
    except DocumentError: raise
    except Exception: raise DocumentError('corrupt_document', 'PDF 结构暂时不可读取，原件已保存。')


def catalog(path):
    ext = Path(path).suffix.lower().removeprefix('.')
    if ext in IMAGE_EXTENSIONS:
        validate_file(path, ext)
        return {'page_count': 1, 'labels': {}, 'catalog_only': True, 'source_type': 'image'}
    reader = reader_for(path)
    labels = {}
    # pypdf synthesizes 1..N when PageLabels is absent; those are not printed
    # page numbers and must never be presented as such.
    if reader.trailer['/Root'].get('/PageLabels'):
        try:
            labels = {str(i): str(label)[:20] for i, label in enumerate(reader.page_labels, 1)}
        except Exception: pass
    return {'page_count': len(reader.pages), 'labels': labels, 'catalog_only': True}


def parse_page(path, index, workdir):
    ext = Path(path).suffix.lower().removeprefix('.')
    if ext in IMAGE_EXTENSIONS:
        validate_file(path, ext)
        if index != 1: raise DocumentError('invalid_page', '图片笔记只有一页。')
        blocks, ocr = native_ocr(path, workdir)
        for value in blocks: value['page'] = 1
        text = '\n\n'.join(b['text'] for b in blocks)
        if not text.strip(): raise DocumentError('empty_text', '本页未提取到有效文字，请查看原图。')
        if len(text) > MAX_TEXT: raise DocumentError('text_limit', '本页文字超出安全上限，原图保留。')
        return {'extracted_text': text, 'blocks': blocks, 'metadata': {
            'extractor_version': 'school-images-v1', 'page_index': 1, 'source_type': 'image',
            'extractor': 'windows.media.ocr', 'ocr': ocr, 'quality': 'ocr_unverified',
            'needs_review': True, 'text_layer_available': False, 'printed_page_label': None}}
    reader = reader_for(path)
    if not 1 <= index <= len(reader.pages): raise DocumentError('invalid_page', 'PDF 没有这一页。')
    page, footer = reader.pages[index - 1], []
    height = float(page.mediabox.height)
    def visitor(text, _cm, tm, _font, size):
        if len(tm) >= 6 and (float(tm[5]) < height * .09 or float(tm[5]) > height * .94) and float(size or 100) <= 14:
            if re.fullmatch(r'\s*(?:\d{1,4}|[ivxlcdmIVXLCDM]{1,10})\s*', text or ''):
                footer.append(text.strip())
    try: text = page.extract_text(visitor_text=visitor) or ''
    except Exception: text = ''
    bad = sum(ch == '\ufffd' or 0xE000 <= ord(ch) <= 0xF8FF or ch == '\x00' for ch in text)
    reliable_layer = len(re.sub(r'\s', '', text)) >= 20 and bad / max(1, len(text)) < .02
    metadata = {'extractor_version': 'school-pages-v1', 'page_index': index, 'needs_review': False,
                'text_layer_available': reliable_layer, 'printed_page_label': footer[-1] if len(set(footer)) == 1 else None}
    if reliable_layer:
        blocks = split_text(text, 'pdf', page=index, section=f'PDF 第 {index} 页')
        metadata.update(extractor='pypdf', quality='text_extracted')
    else:
        image = Path(workdir) / f'page-{index}.png'
        render_pdf(path, index, image, max_pages=2000)
        blocks, ocr = native_ocr(image, workdir)
        for value in blocks: value['page'] = index
        text = '\n\n'.join(b['text'] for b in blocks)
        metadata.update(extractor='windows.media.ocr', ocr=ocr, quality='ocr_unverified', needs_review=True)
    if not blocks or not text.strip(): raise DocumentError('empty_text', '本页未提取到有效文字，请查看原页。')
    if len(text) > MAX_TEXT: raise DocumentError('text_limit', '本页文字超出安全上限，原页保留。')
    return {'extracted_text': text, 'blocks': blocks, 'metadata': metadata}
