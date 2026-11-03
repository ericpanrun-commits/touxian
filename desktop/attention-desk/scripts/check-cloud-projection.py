"""Read existing data and validate the exact cloud projection without publishing it."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.personal_store import PersonalStore
from server.cloud_sync.projection import CloudProjection

parser=argparse.ArgumentParser();parser.add_argument('--database',required=True);parser.add_argument('--cache',required=True);parser.add_argument('--report',required=True)
args=parser.parse_args()
projection=CloudProjection(PersonalStore(args.database),Path(args.cache))
resources,documents=projection.resources();records=projection.records()
assert len({(item['kind'],item['id']) for item in records})==len(records),'Duplicate record identities'
value={'kinds':dict(Counter(item['kind'] for item in records)),'records':len(records),'serialized_bytes':len(json.dumps(records,ensure_ascii=False).encode()),
       'originals':len(resources),'original_bytes':sum(item['bytes'] for item in resources),'documents':len(documents),'pages':sum(item['page_count'] or 0 for item in documents),'source_database_written':False}
Path(args.report).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(value))
