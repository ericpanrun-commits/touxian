"""Explicit offline backup/manifest/bootstrap tooling. Never starts network work."""
import argparse
import json
from pathlib import Path

from .cloud_store import database_manifest, safe_backup, verify_migration
from .notifications.store import NotificationStore
from .personal_store import PersonalStore


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['backup','manifest','bootstrap','compare','prepare'])
    parser.add_argument('--database',required=True)
    parser.add_argument('--destination')
    parser.add_argument('--manifest')
    parser.add_argument('--keep',type=int,default=7)
    args=parser.parse_args()
    path=Path(args.database).resolve()
    if not path.is_file():
        raise SystemExit('指定数据库不存在，未建立替代空库。')
    if args.action=='prepare':
        if not args.destination:
            raise SystemExit('需要单独的准备目录，不能原地修改源数据库。')
        destination=Path(args.destination).resolve()
        before=safe_backup(path,destination/'before',keep=args.keep)
        target=safe_backup(before,destination/'import',keep=args.keep)
        personal=PersonalStore(target)
        personal.initialize()
        # Preserve old rows first; baseline recovery is an explicitly reported safety step.
        verify_migration(before,target)
        NotificationStore(personal).establish_baseline(ignore_disabled=True)
        manifest=database_manifest(target)
        manifest_path=target.with_suffix('.manifest.json')
        manifest_path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        report={'backup':str(before),'import_database':str(target),'manifest':str(manifest_path),
                'original_rows_preserved':True,'source_counts':database_manifest(before)['counts'],
                'destination_counts':manifest['counts'],'database_id':manifest['database_id'],
                'bark_activation_at':manifest['bark_activation_at'],'integrity_check':'ok'}
        (destination/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
    elif args.action=='backup':
        if not args.destination or not 1<=args.keep<=14:
            raise SystemExit('需要备份目录和 1–14 的保留数量。')
        target=safe_backup(path,args.destination,keep=args.keep)
        print(json.dumps({'backup':str(target),'integrity_check':'ok'}))
    elif args.action=='bootstrap':
        personal=PersonalStore(path)
        personal.initialize()
        NotificationStore(personal).establish_baseline(ignore_disabled=True)
        print(json.dumps({'bootstrap':'complete','manifest':database_manifest(path)}))
    elif args.action=='manifest':
        result=database_manifest(path)
        if args.destination:
            Path(args.destination).write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result))
    else:
        if not args.manifest:
            raise SystemExit('需要迁移前 manifest。')
        expected=json.loads(Path(args.manifest).read_text(encoding='utf-8'))
        actual=database_manifest(path)
        matched=expected==actual
        print(json.dumps({'matched':matched,'source_counts':expected['counts'],'destination_counts':actual['counts']}))
        if not matched:
            raise SystemExit(1)


if __name__=='__main__':
    main()
