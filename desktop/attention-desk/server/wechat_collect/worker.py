"""Killable structural probe process. Protocol has no input or sending commands."""
import asyncio
import json
import sys

from ..personal_store import PersonalError


async def probe(policy):
    permission=policy.require(operation='uia',maximum_seconds=15)
    process=await asyncio.create_subprocess_exec(sys.executable,'-m','server.wechat_collect.worker',
                                                 stdin=asyncio.subprocess.DEVNULL,
                                                 stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        stdout,_=await asyncio.wait_for(process.communicate(),timeout=min(15,max(0,(permission.expires_at-policy.clock()).total_seconds())))
        if process.returncode or len(stdout)>100_000:
            raise PersonalError('UIA 诊断未完成。','probe_error',503)
        return json.loads(stdout)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


if __name__=='__main__':
    from .spike import inspect_structure
    print(json.dumps(inspect_structure(),ensure_ascii=False))
