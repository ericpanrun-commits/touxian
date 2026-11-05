"""Prepare a local health overlay on an explicitly inspected API snapshot.

The snapshot must contain an eric-app-api directory and a manifest.json with
the actual deployed version, bundle digest and SHA-256 for each source file.
This script does not contact Supabase or deploy anything. Recheck the live
version and digest immediately before any separately approved deployment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts'
BASE_FILES = {'index.ts', 'handler.ts', 'store.ts', 'files.ts', 'vision.ts', 'views.ts', 'deno.json'}
ADDED_FILES = ('official_health.ts', 'official_health_db.ts', 'deno.lock')


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_snapshot(path: Path):
    snapshot = path.resolve(strict=True)
    if not snapshot.is_relative_to(ARTIFACTS.resolve()):
        raise ValueError('snapshot must be under the local artifacts directory')
    manifest = json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('slug') != 'eric-app-api'
            or type(manifest.get('version')) is not int or manifest['version'] < 1
            or not re.fullmatch(r'[0-9a-f]{64}', manifest.get('ezbr_sha256', ''))):
        raise ValueError('invalid deployed function metadata')
    expected = manifest.get('files_sha256')
    source = snapshot / 'eric-app-api'
    if not isinstance(expected, dict) or set(expected) != BASE_FILES:
        raise ValueError('unexpected snapshot manifest members')
    if {p.name for p in source.iterdir()} != BASE_FILES:
        raise ValueError('unexpected snapshot directory members')
    contents = {name: (source / name).read_bytes() for name in BASE_FILES}
    if any(sha256(contents[name]) != expected[name] for name in BASE_FILES):
        raise ValueError('snapshot does not match its manifest')
    return snapshot, manifest, contents


def prepare(snapshot_path: Path) -> Path:
    snapshot, manifest, original = checked_snapshot(snapshot_path)
    source = ROOT / 'supabase/functions/eric-app-api'
    handler = original['handler.ts'].decode('utf-8')
    index = original['index.ts'].decode('utf-8')
    local_handler = (source / 'handler.ts').read_text(encoding='utf-8')
    local_index = (source / 'index.ts').read_text(encoding='utf-8')

    old_signature = 'clock?:()=>Date}) {'
    new_signature = 'clock?:()=>Date;officialHealth?:(workspace:string)=>Promise<any>}) {'
    anchor = '    const principal=await store.worker(req),workspace=principal.workspace.id;\n'
    route_match = re.search(r"    if\(path==='/worker/official-health'.*?\n    }", local_handler, re.S)
    callback_match = re.search(r'  officialHealth:.*?' + re.escape('\n  })(),'), local_index, re.S)
    if (handler.count(old_signature) != 1 or handler.count(anchor) != 1
            or '/worker/official-health' in handler or 'officialHealth:' in index
            or index.count('  visionSecret:') != 1 or route_match is None or callback_match is None):
        raise ValueError('snapshot or health overlay differs from the reviewed contract')
    route, callback = route_match.group(0), callback_match.group(0)
    updated_handler = handler.replace(old_signature, new_signature, 1).replace(anchor, anchor + route + '\n', 1)
    updated_index = index.replace('  visionSecret:', callback + '\n  visionSecret:', 1)
    if (updated_handler.replace(anchor + route + '\n', anchor, 1).replace(new_signature, old_signature, 1) != handler
            or updated_index.replace(callback + '\n  visionSecret:', '  visionSecret:', 1) != index):
        raise ValueError('overlay would change unrelated deployed code')
    added = {name: (source / name).read_bytes() for name in ADDED_FILES}

    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    output = ARTIFACTS / 'p0-20261104' / f"candidate-api{manifest['version']}-health-{stamp}-{uuid4().hex[:8]}"
    package = output / 'eric-app-api'
    package.mkdir(parents=True)
    candidate = {**original, **added, 'handler.ts': updated_handler.encode('utf-8'), 'index.ts': updated_index.encode('utf-8')}
    for name, data in candidate.items():
        (package / name).write_bytes(data)
    summary = {
        'prepared_at': datetime.now(timezone.utc).isoformat(),
        'base_snapshot': str(snapshot),
        'base_api_version': manifest['version'],
        'base_ezbr_sha256': manifest['ezbr_sha256'],
        'base_updated_at': manifest.get('updated_at'),
        'directory': str(package),
        'changed': ['index.ts', 'handler.ts'],
        'added': list(ADDED_FILES),
        'preserved_files_sha256': {name: sha256(original[name]) for name in sorted(BASE_FILES - {'index.ts', 'handler.ts'})},
        'candidate_files_sha256': {name: sha256(data) for name, data in sorted(candidate.items())},
        'deployment_performed': False,
        'live_version_rechecked': False,
    }
    (output / 'manifest.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True, help='inspected API snapshot directory under artifacts')
    args = parser.parse_args()
    output = prepare(args.snapshot)
    print(json.dumps({'candidate': str(output), 'deployment_performed': False, 'live_version_rechecked': False}))


if __name__ == '__main__':
    main()
