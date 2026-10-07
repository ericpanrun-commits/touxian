from pathlib import Path, PurePosixPath
import warnings
import zipfile
from PIL import Image

SOFT_BYTES = 15_000_000
MAX_BYTES = 50_000_000
MAX_EXPANDED = 60_000_000
MAX_PAGES = 200
MAX_TEXT = 1_000_000
MAX_PIXELS = 30_000_000
MIMES = {'pdf': 'application/pdf', 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         'txt': 'text/plain', 'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg'}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class DocumentError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def filename(value):
    value = str(value or '').replace('\\', '/').rsplit('/', 1)[-1]
    return ''.join(c for c in value if ord(c) >= 32 and c not in '\x7f')[:180] or '未命名文件'


def extension(name):
    return Path(name).suffix.lower().removeprefix('.')


def check_docx(path):
    try:
        with zipfile.ZipFile(path) as z:
            rows = z.infolist()
            if len(rows) > 2000 or sum(i.file_size for i in rows) > MAX_EXPANDED:
                raise DocumentError('decompression_limit', 'DOCX 解压内容超过安全上限，原文件保留。')
            for i in rows:
                if i.flag_bits & 1 or i.file_size > 20_000_000 or i.file_size > max(1, i.compress_size) * 250:
                    raise DocumentError('decompression_limit', 'DOCX 压缩内容异常，未解析。')
                parts = PurePosixPath(i.filename.replace('\\', '/'))
                if parts.is_absolute() or '..' in parts.parts or i.filename.lower().endswith(('vbaproject.bin', '.exe', '.js')):
                    raise DocumentError('unsafe_document', '文档含不支持的路径、宏或执行内容，未解析。')
            if not {'[Content_Types].xml', 'word/document.xml'} <= set(z.namelist()):
                raise DocumentError('corrupt_document', '文件不是标准 DOCX 文档。')
    except (zipfile.BadZipFile, OSError):
        raise DocumentError('corrupt_document', 'DOCX 文件损坏，原文件保留。')


def validate_file(path, ext, declared_mime=None):
    if ext not in MIMES:
        raise DocumentError('unsupported', '仅支持 PDF、DOCX、TXT、PNG、JPG / JPEG，原文件未执行。')
    mime = (declared_mime or '').split(';')[0].strip().lower()
    if mime not in {'', 'application/octet-stream', 'binary/octet-stream', MIMES[ext], *({'application/zip'} if ext == 'docx' else set())}:
        raise DocumentError('mime_mismatch', '文件类型与扩展名不一致，未解析。')
    head = Path(path).read_bytes()[:1024] if Path(path).stat().st_size <= 1024 else open_head(path)
    if ext == 'pdf' and not head.lstrip().startswith(b'%PDF-'):
        raise DocumentError('mime_mismatch', '文件内容不是 PDF，原文件保留。')
    if ext == 'docx':
        check_docx(path)
    if ext in {'png', 'jpg', 'jpeg'}:
        expected = b'\x89PNG\r\n\x1a\n' if ext == 'png' else b'\xff\xd8\xff'
        if not head.startswith(expected):
            raise DocumentError('mime_mismatch', '图片内容与格式不符，未解析。')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    if image.width * image.height > MAX_PIXELS:
                        raise DocumentError('image_too_large', '图片像素超过 3,000 万上限，原图保留。')
                    image.verify()
        except DocumentError:
            raise
        except Exception:
            raise DocumentError('corrupt_document', '图片损坏或像素异常，原图保留。')
    if ext == 'txt' and (b'\x00' in head or head.startswith((b'MZ', b'PK\x03\x04'))):
        raise DocumentError('mime_mismatch', '文件不是可读文本，未解析。')
    return MIMES[ext]


def open_head(path):
    with open(path, 'rb') as f:
        return f.read(1024)
