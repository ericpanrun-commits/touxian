"""Prepare a protected identity; register its descriptor before explicitly enabling it."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.cloud_sync.settings import WorkerSettings

parser=argparse.ArgumentParser()
parser.add_argument('--settings',required=True)
parser.add_argument('--workspace',required=True)
parser.add_argument('--notes-installation',required=True)
parser.add_argument('--enable-registered',action='store_true')
args=parser.parse_args()
settings=WorkerSettings(args.settings)
descriptor=settings.prepare(args.workspace,args.notes_installation)
if args.enable_registered:settings.enable()
print(json.dumps(descriptor))
