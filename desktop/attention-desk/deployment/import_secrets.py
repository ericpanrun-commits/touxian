"""Operator-only, AFTER cost approval. Values go to Fly stdin, never argv/logs."""
import argparse
import json
import secrets
import subprocess
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values  # noqa: E402
from server.cloud_store import database_manifest  # noqa: E402
from server.notifications.config import BarkConfig  # noqa: E402


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',required=True,choices=['zqu-workspace-worker'])
    parser.add_argument('--fly',default=str(Path.home()/'.fly/bin/fly.exe'))
    parser.add_argument('--database',required=True)
    parser.add_argument('--bark-env',default='server/.env')
    parser.add_argument('--proxy-env',default='artifacts/cloud-operator.env')
    args=parser.parse_args()
    # An existing app must have been created separately with explicit approval.
    check=subprocess.run([args.fly,'apps','list','--json'],capture_output=True,text=True)
    if check.returncode or not any(app.get('name')==args.app for app in json.loads(check.stdout)):
        raise SystemExit('Worker app not found; no resource was created and no secret was written.')
    config=BarkConfig.from_env(dotenv_values(args.bark_env,interpolate=False))
    if not config.configured or config.error:
        raise SystemExit('Bark configuration is missing or invalid; no values are displayed.')
    manifest=database_manifest(args.database)
    if not manifest['database_id'] or not manifest['bark_activation_at']:
        raise SystemExit('Run offline preparation and verify the production identity/baseline first.')
    proxy=Path(args.proxy_env).resolve()
    if proxy.exists():
        token=dotenv_values(proxy,interpolate=False).get('CLOUD_ADMIN_TOKEN','')
        if len(token)<32:raise SystemExit('Existing operator token file is invalid; it was not overwritten.')
    else:
        proxy.parent.mkdir(parents=True,exist_ok=True)
        token=secrets.token_urlsafe(48)
        with proxy.open('x',encoding='utf-8') as handle:
            handle.write('ATTENTION_CLOUD_API_URL=https://'+args.app+'.fly.dev\nCLOUD_ADMIN_TOKEN='+token+'\n')
        if sys.platform!='win32':proxy.chmod(0o600)
    values={'CLOUD_ADMIN_TOKEN':token,'PRODUCTION_DATABASE_ID':manifest['database_id'],
            'BARK_DEVICE_KEY':config.device_key,'BARK_SERVER_URL':config.server_url,'BARK_GROUP':config.group}
    if any('\n' in value or '\r' in value for value in values.values()):
        raise SystemExit('Invalid multiline secret; no values are displayed.')
    result=subprocess.run([args.fly,'secrets','import','--app',args.app,'--stage'],
        input=''.join(key+'='+value+'\n' for key,value in values.items()),capture_output=True,text=True)
    if result.returncode:
        raise SystemExit('Fly secret staging failed. Provider output was withheld to avoid credential disclosure.')
    print(json.dumps({'app':args.app,'secret_names':sorted(values),'staged':True,'activated':False,
                      'operator_file':str(proxy)}))


if __name__=='__main__':main()
