"""Container PID 1: validate the real mount, drop privileges, exec one Uvicorn."""
import logging
import os
import sys

from .cloud_store import validate_storage
from .runtime import RuntimeConfig


def main():
    runtime = RuntimeConfig.from_env()
    root,database = validate_storage(runtime)
    if os.name != 'nt' and os.getuid()==0:
        # Exact owned paths only. Never recursively chown an arbitrary volume.
        os.chown(root,10001,10001)
        for path in [database, database.with_name(database.name+'-wal'),database.with_name(database.name+'-shm')]:
            if path.exists():
                if path.is_symlink() or path.resolve().parent!=root:
                    raise RuntimeError('生产文件路径不符合安全约束。')
                os.chown(path,10001,10001)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO)
    os.execv(sys.executable,[sys.executable,'-m','uvicorn','server.cloud_app:app','--host','0.0.0.0','--port','8080','--workers','1','--no-access-log'])


if __name__=='__main__':
    main()
