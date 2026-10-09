"""Explicit isolated loopback UI fixture; never used by desktop startup.

Run with --data pointing at a disposable directory. Uses fake AI and local
original/parsing services. Does not detect or modify Windows networking.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import uvicorn
from ..personal_store import PersonalStore
from ..documents.service import DocumentService
from ..extraction_service import ExtractionService
from ..extraction_provider import ProviderResult
from ..extraction_errors import ExtractionFailure
from ..test_extraction_review import TestSettings
from .security import PairingAuthority
from .submissions import SubmissionService
from .app import create_app


class QAProvider:
    name = 'deepseek'
    async def extract(self, snapshot, config, key):
        await asyncio.sleep(2)
        if 'FAIL_AI' in snapshot['original_text']:
            raise ExtractionFailure('provider_unavailable', '隔离测试的模拟失败')
        return ProviderResult(json.dumps({'events':[dict(id='e1',title='隔离测试的通知',summary='',category='测试',audience=None,participation='required',notice_kind='new',eligibility=[],deadlines=[],actions=[],materials=[],locations=[],uncertainties=[])], 'evidence':[]},ensure_ascii=False),{},'QA-fake-provider')


async def main(data, port):
    data.mkdir(parents=True, exist_ok=True)
    store=PersonalStore(data/'personal.sqlite3');store.initialize()
    docs=DocumentService(store);extraction=ExtractionService(store,TestSettings(),QAProvider())
    service=SubmissionService(store,docs,extraction);authority=PairingAuthority()
    token=authority.regenerate()
    origin=f'http://127.0.0.1:{port}'
    (data/'pairing-qa.json').write_text(json.dumps({'url':origin+'/pair?token='+token,'fixture':True}),encoding='utf-8')
    app=create_app(service,authority,origin,Path(__file__).resolve().parents[2],allow_loopback_fixture=True)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,access_log=False,log_config=None,log_level='critical'))
    try: await server.serve()
    finally:
        (data/'pairing-qa.json').unlink(missing_ok=True)
        await service.close();await extraction.close();await asyncio.to_thread(docs.close)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);parser.add_argument('--port',type=int,default=18473)
    args=parser.parse_args()
    asyncio.run(main(args.data.resolve(),args.port))
