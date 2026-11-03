"""Copy verified existing originals/read models to the owner's private cloud; no local writes."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import sys
import threading
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.cloud_sync.service import CloudSync,SyncError
from server.documents.service import DocumentService
from server.personal_store import PersonalStore
from server.cloud_sync.settings import WorkerSettings
from types import SimpleNamespace


class ReadRenderer:
    worker=DocumentService.worker
    def __init__(self):self.lock=threading.Lock();self.children=set();self.stopping=threading.Event()
    def close(self):
        self.stopping.set()
        with self.lock:
            for child in self.children:
                if child.poll() is None:child.kill()


async def main(args):
    source=PersonalStore(Path(args.database).resolve());settings=WorkerSettings(args.settings or source.path.with_name('cloud-worker-settings.json'))
    renderer=ReadRenderer();service=CloudSync(source,None,SimpleNamespace(documents=renderer),None,settings,cache=Path(args.cache))
    service.config=settings.read()
    service.process_lock.acquire()
    async def progress():
        while True:
            print(json.dumps({'state':'copying','confirmed_resources':len(service.resources)},ensure_ascii=False),flush=True)
            await asyncio.sleep(20)
    progress_task=asyncio.create_task(progress())
    try:
        response=await service.request('POST','/worker/hello',{'epoch':service.epoch})
        service.workspace=response['workspace_id'];service.state['phase']='preparing'
        await service.resource_catalogue()
        service.heartbeat=asyncio.create_task(service._heartbeats())
        if response.get('record_catalogue'):await service.record_catalogue()
        failures=0
        while True:
            try:
                await service.publish();break
            except (httpx.TransportError,SyncError) as error:
                if isinstance(error,SyncError) and error.status<500 and error.status!=429:raise
                failures+=1
                print(json.dumps({'state':'retrying','confirmed_resources':len(service.resources),'reason':getattr(error,'code','connection_interrupted'),'attempt':failures}),flush=True)
                old=service.client;service.client=service._new_client();await old.aclose()
                await asyncio.sleep(min(60,2**min(failures,6)))
        await service.request('POST','/worker/heartbeat',{'epoch':service.epoch,'phase':'connected','published':True})
        summary={'state':'published','workspace_id':service.workspace,'confirmed_resources':len(service.resources),'published_records':len(service.hashes),'local_database_written':False}
        Path(args.report).write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(summary,ensure_ascii=False),flush=True)
    finally:
        renderer.close();progress_task.cancel();await asyncio.gather(progress_task,return_exceptions=True);await service.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True);parser.add_argument('--cache',required=True);parser.add_argument('--report',required=True);parser.add_argument('--settings')
    asyncio.run(main(parser.parse_args()))
