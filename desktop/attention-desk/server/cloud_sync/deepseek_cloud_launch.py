"""Desktop startup hook; absent/revoked independent cloud consent is a no-op."""
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import subprocess
import sys

from .deepseek_cloud_continuous import continuous_approval
from .wechat_dispatch_authority import DispatchAuthority,DispatchDenied


def launch_automatic_if_authorized(directory):
    from ..wechat_collect.automation import FILENAME
    if not (Path(directory)/FILENAME).is_file():return None
    return launch_if_authorized(directory)


def launch_if_authorized(directory):
    directory=Path(directory).resolve()
    from ..wechat_collect.automation import FILENAME,POLICY
    if (directory/FILENAME).is_file():
        try:
            from .notification_runtime import load
            cache,_,_=load(directory)
        except (OSError,ValueError,KeyError,TypeError,DispatchDenied):return None
        from ..notification_supervisor import adopt
        prior=adopt(cache/'status.json',POLICY)
        if prior:return prior
        root=Path(__file__).resolve().parents[2]
        return subprocess.Popen([sys.executable,str(root/'scripts/notification-automation.py'),
            '--directory',str(directory),'--run'],cwd=root,stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
    cache=directory/'cloud-sync/deepseek-continuous'
    if os.name!='nt' or not all((cache/name).is_file() for name in
        ('approval.json','release.json','authority.sqlite3','runtime.sqlite3','journal.json')):
        return None
    try:
        scope=continuous_approval((cache/'approval.json').read_bytes())
        authority=DispatchAuthority(cache/'authority.sqlite3')
        with closing(sqlite3.connect(authority.path.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row;authority._grant(db,scope.approval_id,scope.binding())
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,DispatchDenied):
        return None
    root=Path(__file__).resolve().parents[2]
    # The child checks pinned release/evidence and takes its own process lock.
    # Never starts Collector or the full CloudSync service, or edits controls.
    return subprocess.Popen([sys.executable,str(root/'scripts/deepseek-cloud-worker.py'),
        '--directory',str(directory),'--run'],cwd=root,stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True,
        # DETACHED_PROCESS would make Windows ignore CREATE_NO_WINDOW.
        creationflags=subprocess.CREATE_NO_WINDOW)
