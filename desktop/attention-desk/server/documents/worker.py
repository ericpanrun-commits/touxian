"""Isolated parser process: a bad document never runs in the API process."""
import json
import sys
from pathlib import Path
from .parsers import parse_document, render_pdf
from .policy import DocumentError


def main():
    path, ext, destination = sys.argv[1:4]
    workdir = Path(destination).parent
    try:
        from .resources import limit_parser
        limit_parser()
        if ext.startswith('render:'):
            render_pdf(path, int(ext.split(':')[1]), destination)
            return
        result = parse_document(path, ext, workdir)
    except DocumentError as e:
        result = {'error': {'code': e.code, 'message': str(e)}}
    except Exception:
        result = {'error': {'code': 'parse_failed', 'message': '文件解析失败，原文件保留，可以重试或手动整理。'}}
    Path(destination).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
