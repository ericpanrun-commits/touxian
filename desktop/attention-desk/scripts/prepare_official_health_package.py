"""Prepare a minimal API health overlay on an inspected deployed-source backup.

This does not deploy. Existing live Vision, intake and error handling stay as
downloaded, even if the local branch has unrelated changes to those modules.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    baseline = ROOT / 'artifacts/p0-20261104/deployed-api18/eric-app-api'
    source = ROOT / 'supabase/functions/eric-app-api'
    stamp = datetime.now(timezone.utc).strftime('%H%M%S')
    output = ROOT / 'artifacts/p0-20261104' / ('candidate-api18-health-' + stamp + '-' + uuid4().hex[:8]) / 'eric-app-api'
    output.mkdir(parents=True)
    for item in baseline.iterdir():
        if item.name not in {'index.ts','handler.ts','store.ts','files.ts','vision.ts','views.ts','deno.json'}:
            raise ValueError('unexpected deployed package member')
        shutil.copyfile(item, output / item.name)
    handler = (output / 'handler.ts').read_text(encoding='utf-8')
    old_signature = 'clock?:()=>Date}) {'
    new_signature = 'clock?:()=>Date;officialHealth?:(workspace:string)=>Promise<any>}) {'
    if handler.count(old_signature) != 1:
        raise ValueError('deployed handler signature changed')
    handler = handler.replace(old_signature, new_signature, 1)
    current_handler = (source / 'handler.ts').read_text(encoding='utf-8')
    route = re.search(r"    if\(path==='/worker/official-health'.*?\n    }", current_handler, re.S).group(0)
    anchor = '    const principal=await store.worker(req),workspace=principal.workspace.id;\n'
    if handler.count(anchor) != 1:
        raise ValueError('deployed worker authorization changed')
    handler = handler.replace(anchor, anchor + route + '\n', 1)
    (output / 'handler.ts').write_text(handler, encoding='utf-8')
    index = (output / 'index.ts').read_text(encoding='utf-8')
    current_index = (source / 'index.ts').read_text(encoding='utf-8')
    callback = re.search(r'  officialHealth:.*?' + re.escape('\n  })(),'), current_index, re.S).group(0)
    if index.count('  visionSecret:') != 1:
        raise ValueError('deployed entrypoint changed')
    (output / 'index.ts').write_text(index.replace('  visionSecret:', callback + '\n  visionSecret:', 1), encoding='utf-8')
    for name in ('official_health.ts', 'official_health_db.ts', 'deno.lock'):
        shutil.copyfile(source / name, output / name)
    untouched = ('store.ts','files.ts','vision.ts','views.ts','deno.json')
    preserved = {name: hashlib.sha256((output/name).read_bytes()).hexdigest() for name in untouched}
    if not all((baseline/name).read_bytes() == (output/name).read_bytes() for name in untouched):
        raise ValueError('unrelated deployed source changed')
    result = {'prepared_at': datetime.now(timezone.utc).isoformat(), 'base_api_version': 18,
        'directory': str(output), 'preserved_files_sha256': preserved,
        'changed': ['index.ts','handler.ts'], 'added': ['official_health.ts','official_health_db.ts','deno.lock'],
        'deployment_performed': False}
    (ROOT/'artifacts/p0-20261104/official-api-candidate.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
