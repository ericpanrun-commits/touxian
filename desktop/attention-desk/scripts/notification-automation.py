"""Prepare/check only by default; activation needs an approved plan and remote proof."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.cloud_sync.notification_runtime import prepare,activate,load,run

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True)
    modes=p.add_mutually_exclusive_group()
    modes.add_argument('--prepare',action='store_true');modes.add_argument('--activate',action='store_true');modes.add_argument('--run',action='store_true')
    p.add_argument('--spec',type=Path);p.add_argument('--api-manifest',type=Path);p.add_argument('--plan',type=Path);p.add_argument('--proof',type=Path);p.add_argument('--output',type=Path)
    a=p.parse_args()
    def read(path):return json.loads(path.read_text('utf-8'))
    if a.prepare:
        if not all((a.spec,a.api_manifest,a.output)):p.error('prepare requires spec, api-manifest and isolated output')
        value=prepare(a.directory,read(a.spec),read(a.api_manifest))
        if a.output.exists() or a.output.resolve().is_relative_to(a.directory.resolve()):raise ValueError('new_isolated_plan_output_required')
        a.output.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf-8');print('prepared_only_no_activation')
    elif a.activate:
        if not a.plan or not a.proof:p.error('activate requires approved plan and fresh matching deployment proof')
        print(json.dumps(activate(a.directory,read(a.plan),read(a.proof))))
    elif a.run:asyncio.run(run(a.directory))
    else:load(a.directory);print('checked_no_network_requests')
