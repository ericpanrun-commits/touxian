"""Text/page/table extraction; native Windows OCR, with no cloud images."""
from pathlib import Path
import json
import os
import subprocess
import re
from PIL import Image, ImageOps
from pypdf import PdfReader
from docx import Document
from docx.oxml.ns import qn
import pypdfium2 as pdfium
from .policy import DocumentError, MAX_PAGES, MAX_TEXT, MAX_PIXELS, validate_file

VERSION = 'documents-1'
CHUNK = 2500


def block(text, source_type, *, page=None, section=None, bbox=None, words=None, review=False):
    return {'text': text, 'source_type': source_type, 'page': page, 'section': section,
            'bounding_box': bbox, 'words': words or [], 'needs_review': review}


def split_text(text, kind, *, page=None, section=None):
    # Each stored chunk is immutable and independently quoteable, including page.
    values = []
    for offset in range(0, len(text), CHUNK):
        part = text[offset:offset + CHUNK]
        if part.strip():
            values.append(block(part, kind, page=page, section=section + f' · 文本块 {len(values) + 1}' if len(text) > CHUNK and section else section))
    return values


def render_pdf(path, page_number, destination):
    with pdfium.PdfDocument(str(path)) as pdf:
        if not 1 <= page_number <= len(pdf) or len(pdf) > MAX_PAGES:
            raise DocumentError('page_limit', 'PDF 页数或所选页码超出上限。')
        page = pdf[page_number - 1]
        try:
            width, height = page.get_size()
            if width <= 0 or height <= 0 or width * height > 50_000_000:
                raise DocumentError('page_limit', 'PDF 页面尺寸异常，未渲染。')
            scale = min(2.4, 2400 / max(width, height))
            bitmap = page.render(scale=scale, may_draw_forms=False)
            try:
                image = bitmap.to_pil().convert('RGB')
                image.save(destination, format='PNG')
            finally:
                bitmap.close()
        finally:
            page.close()
    return str(destination)


def native_ocr(path, workdir):
    if os.name != 'nt':
        raise DocumentError('ocr_unavailable', '本阶段本地 OCR 使用 Windows 识别服务；当前系统不可用。')
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
    if image.width * image.height > MAX_PIXELS:
        raise DocumentError('image_too_large', '图片像素超过上限，原图保留。')
    original_size = image.size
    scale = min(1, 2400 / image.width)
    if scale < 1:
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    lines, languages = [], set()
    powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = Path(__file__).with_name('windows_ocr.ps1')
    # Tile long screenshots instead of shrinking all their text into one image.
    for top in range(0, image.height, 2100):
        tile = image.crop((0, top, image.width, min(image.height, top + 2400)))
        tile_path = Path(workdir) / f'ocr-tile-{top}.png'; tile.save(tile_path)
        try:
            completed = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-ImagePath', str(tile_path.resolve())],
                                       capture_output=True, timeout=30, encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
            result = json.loads(completed.stdout.lstrip('\ufeff'))
        except (subprocess.TimeoutExpired, ValueError, OSError):
            raise DocumentError('ocr_failed', '本地 OCR 未完成，原文件保留，可重试。')
        if completed.returncode or result.get('error'):
            raise DocumentError('ocr_failed', '本地 OCR 未完成，请检查 Windows 文字识别语言，原文件保留。')
        languages.add(result['language'])
        for row in result['lines']:
            raw_text, words, cursor = row['text'], [], 0
            text = re.sub(r'(?<=[\u4e00-\u9fff0-9])\s+(?=[\u4e00-\u9fff0-9，。；：、！？])', '', raw_text)
            if not text.strip() or not row['words']:
                continue
            min_y = min(w['y'] for w in row['words'])
            center = (min_y+max(w['y']+w['height'] for w in row['words']))/2
            if (top and center<150) or (top+2400<image.height and center>=2250):
                # Previous tile owned the overlap.
                continue
            for word in row['words']:
                start = text.find(word['text'], cursor)
                if start < 0: continue
                cursor = start + len(word['text'])
                words.append({'start': start, 'end': cursor, 'box': {'x': word['x'] / scale, 'y': (word['y'] + top) / scale,
                              'width': word['width'] / scale, 'height': word['height'] / scale,
                              'image_width': original_size[0], 'image_height': original_size[1]}})
            if not words: continue
            boxes = [w['box'] for w in words]
            left, upper = min(b['x'] for b in boxes), min(b['y'] for b in boxes)
            bbox = {'x': left, 'y': upper, 'width': max(b['x'] + b['width'] for b in boxes) - left,
                    'height': max(b['y'] + b['height'] for b in boxes) - upper, 'image_width': original_size[0], 'image_height': original_size[1]}
            value = block(text, 'image_ocr', section=f'OCR 行 {len(lines) + 1}', bbox=bbox, words=words, review=True)
            value['raw_ocr_text'] = raw_text
            lines.append(value)
    if not lines:
        raise DocumentError('ocr_empty', '未识别出有效文字，原文件保留；请换清晰图片或手动输入。')
    quality = 'low' if len(''.join(b['text'] for b in lines)) < 15 or any(b['bounding_box']['height'] < 10 for b in lines) else 'unverified'
    return lines, {'engine': 'windows.media.ocr', 'language': ','.join(sorted(languages)), 'confidence': None, 'quality': quality,
                   'note': 'Windows OCR 不提供置信度；所有 OCR 内容均需要人工核对。'}


def parse_document(path, ext, workdir):
    validate_file(path, ext)
    blocks, metadata = [], {'extractor_version': VERSION, 'needs_review': False}
    if ext == 'txt':
        data = Path(path).read_bytes()
        try: text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            try: text = data.decode('gb18030'); metadata['encoding'] = 'gb18030'
            except UnicodeDecodeError: raise DocumentError('unsupported_encoding', '文本编码无法识别，原文件保留。')
        if not text.strip(): raise DocumentError('empty_text', '文本文件为空，原文件保留。')
        blocks = split_text(text, 'text', section='文本文件'); metadata['extractor'] = 'python-text'
    elif ext == 'docx':
        doc = Document(path); paragraph_count = table_count = 0
        for element in doc.element.body.iterchildren():
            if element.tag == qn('w:p'):
                paragraph_count += 1
                # python-docx provides visible run order; do not execute fields/macros.
                from docx.text.paragraph import Paragraph
                paragraph = Paragraph(element, doc)
                text = paragraph.text
                heading = paragraph.style.name if paragraph.style else ''
                blocks += split_text(text, 'docx', section=f'段落 {paragraph_count}' + (f' · {heading}' if heading.startswith('Heading') else ''))
            elif element.tag == qn('w:tbl'):
                table_count += 1
                from docx.table import Table
                table = Table(element, doc)
                for r, row in enumerate(table.rows, 1):
                    seen = set()
                    for col, cell in enumerate(row.cells, 1):
                        if cell._tc in seen: continue
                        seen.add(cell._tc)
                        # Includes paragraph order and nested table text via OOXML runs.
                        text = '\n'.join(''.join(t.text or '' for t in p.iter(qn('w:t'))) for p in cell._tc.iter(qn('w:p')))
                        blocks += split_text(text, 'docx', section=f'表格 {table_count} · 行 {r} · 列 {col}')
        metadata.update(extractor='python-docx', paragraphs=paragraph_count, tables=table_count)
    elif ext == 'pdf':
        try: reader = PdfReader(path, strict=False)
        except Exception: raise DocumentError('corrupt_document', 'PDF 文件损坏，原文件保留。')
        if reader.is_encrypted:
            raise DocumentError('encrypted_pdf', '加密 PDF 需要密码，本阶段未解析，原文件保留。')
        if not 1 <= len(reader.pages) <= MAX_PAGES:
            raise DocumentError('page_limit', 'PDF 超过 200 页解析上限，原文件保留。')
        scanned, blank = [], []
        for number, page in enumerate(reader.pages, 1):
            try: text = page.extract_text() or ''
            except Exception: raise DocumentError('corrupt_document', f'PDF 第 {number} 页文字层损坏，原文件保留。')
            if len(re.sub(r'\s', '', text)) >= 10:
                blocks += split_text(text, 'pdf', page=number, section=f'第 {number} 页')
            else:
                scanned.append(number)
                image = Path(workdir) / f'page-{number}.png'
                render_pdf(path, number, image)
                with Image.open(image) as raster:
                    low,high=raster.convert('L').getextrema()
                if high-low<8:
                    scanned.pop(); blank.append(number); continue
                values, ocr = native_ocr(image, workdir)
                for b in values: b['page'] = number; b['section'] = f'第 {number} 页 · {b["section"]}'
                blocks += values; metadata['ocr'] = ocr
        metadata.update(extractor='pypdf+windows.media.ocr' if scanned else 'pypdf', page_count=len(reader.pages),
                        scanned_pages=scanned, blank_pages=blank, needs_ocr=bool(scanned), pdf_kind='scanned_pdf' if len(scanned)+len(blank) == len(reader.pages) else 'mixed_pdf' if scanned else 'text_pdf', needs_review=bool(scanned))
    else:
        blocks, ocr = native_ocr(path, workdir)
        metadata.update(extractor='windows.media.ocr', ocr=ocr, needs_review=True)
    if not blocks: raise DocumentError('empty_text', '没有提取到有效文字，原文件保留。')
    if sum(len(b['text']) for b in blocks) > MAX_TEXT:
        raise DocumentError('text_limit', '提取文字超过 100 万字符上限，原文件保留。')
    return {'blocks': blocks, 'metadata': metadata, 'extracted_text': '\n\n'.join(b['text'] for b in blocks)}
