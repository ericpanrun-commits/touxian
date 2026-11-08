import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import shutil
import ssl
import subprocess

import httpx
import pytest

from server.cloud_sync.wechat_prepared_proxy import PreparedLocalProxyBackend
from server.cloud_sync.wechat_dispatch_authority import DispatchDenied


@pytest.fixture(scope='module')
def certificate(tmp_path_factory):
    root=tmp_path_factory.mktemp('synthetic-proxy-tls')
    executable=shutil.which('openssl') or 'C:/Program Files/Git/usr/bin/openssl.exe'
    if not Path(executable).is_file():pytest.fail('OpenSSL is required for the synthetic TLS integration test')
    key,cert=root/'key.pem',root/'cert.pem'
    subprocess.run([executable,'req','-x509','-newkey','rsa:2048','-nodes','-days','1',
        '-keyout',str(key),'-out',str(cert),'-subj','/CN=notice.invalid',
        '-addext','subjectAltName=DNS:notice.invalid'],check=True,capture_output=True)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
    return context,cert.read_text(encoding='ascii')


@asynccontextmanager
async def tunnel(certificate,*,response=b'HTTP/1.1 200 Connection established\r\n\r\n'):
    context,pem=certificate;tasks=set();connects=[];requests=[];encrypted=[]
    async def origin(reader,writer):
        task=asyncio.current_task();tasks.add(task)
        try:
            header=await reader.readuntil(b'\r\n\r\n')
            length=int(next(line.split(b':',1)[1] for line in header.split(b'\r\n') if line.lower().startswith(b'content-length:')))
            requests.append(header+await reader.readexactly(length))
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}');await writer.drain()
        except (OSError,asyncio.IncompleteReadError):pass
        finally:writer.close();tasks.discard(task)
    target=await asyncio.start_server(origin,'127.0.0.1',0,ssl=context)
    target_port=target.sockets[0].getsockname()[1]
    async def proxy(reader,writer):
        task=asyncio.current_task();tasks.add(task);remote=None
        async def pipe(source,destination,*,capture=False):
            while chunk:=await source.read(65536):
                if capture:encrypted.append(chunk)
                destination.write(chunk);await destination.drain()
            destination.close()
        try:
            connects.append(await reader.readuntil(b'\r\n\r\n'))
            writer.write(response);await writer.drain()
            if not response.startswith(b'HTTP/1.1 200'):return
            incoming,remote=await asyncio.open_connection('127.0.0.1',target_port)
            await asyncio.gather(pipe(reader,remote,capture=True),pipe(incoming,writer))
        except (OSError,asyncio.IncompleteReadError):pass
        finally:
            writer.close()
            if remote:remote.close()
            tasks.discard(task)
    gateway=await asyncio.start_server(proxy,'127.0.0.1',0)
    try:yield f'http://127.0.0.1:{gateway.sockets[0].getsockname()[1]}',pem,connects,requests,encrypted
    finally:
        gateway.close();target.close();await gateway.wait_closed();await target.wait_closed()
        for task in tuple(tasks):task.cancel()
        await asyncio.gather(*tuple(tasks),return_exceptions=True)


def test_real_connect_tunnel_sends_no_application_bytes_until_protected_accept(certificate):
    async def scenario():
        async with tunnel(certificate) as (proxy,pem,connects,requests,encrypted):
            backend=PreparedLocalProxyBackend('https://notice.invalid',proxy,ca_pem=pem)
            url=httpx.URL('https://notice.invalid/worker/wechat-publish')
            connection=await backend.prepare(url)
            assert connects==[b'CONNECT notice.invalid:443 HTTP/1.1\r\nHost: notice.invalid:443\r\n\r\n']
            assert requests==[]
            request=httpx.Request('POST',url,headers={'X-Eric-Worker-Key':'synthetic-key'},json={'notice':'synthetic-body'})
            connection.accept(request)
            assert (await connection.response()).status_code==200
            await connection.aclose()
            assert len(requests)==1 and requests[0].endswith(request.content)
            assert b'synthetic-key' not in b''.join(encrypted) and b'synthetic-body' not in b''.join(encrypted)
    asyncio.run(scenario())


@pytest.mark.parametrize('case',['untrusted_ca','wrong_hostname'])
def test_tls_verification_is_not_weakened_for_proxy(certificate,case):
    async def scenario():
        async with tunnel(certificate) as (proxy,pem,connects,requests,_):
            origin='https://other.invalid' if case=='wrong_hostname' else 'https://notice.invalid'
            backend=PreparedLocalProxyBackend(origin,proxy,ca_pem=pem if case=='wrong_hostname' else None)
            with pytest.raises(httpx.ConnectError):await backend.prepare(httpx.URL(origin))
            assert len(connects)==1 and requests==[]
    asyncio.run(scenario())


@pytest.mark.parametrize('response',[b'HTTP/1.1 302 Found\r\nLocation: https://elsewhere.invalid\r\n\r\n',
    b'HTTP/1.1 407 Proxy Authentication Required\r\n\r\n',b'HTTP/1.1 200 OK\r\nX: '+b'a'*9000+b'\r\n\r\n'])
def test_rejected_or_oversized_connect_never_sends_notice(certificate,response):
    async def scenario():
        async with tunnel(certificate,response=response) as (proxy,pem,connects,requests,_):
            backend=PreparedLocalProxyBackend('https://notice.invalid',proxy,ca_pem=pem)
            with pytest.raises(httpx.ConnectError):await backend.prepare(httpx.URL('https://notice.invalid'))
            assert requests==[]
    asyncio.run(scenario())


@pytest.mark.parametrize('proxy',['http://elsewhere.invalid:7897','http://user:secret@127.0.0.1:7897',
    'http://127.0.0.1:7897/path','https://127.0.0.1:7897'])
def test_only_explicit_credential_free_local_proxy_is_accepted(proxy):
    with pytest.raises(ValueError):PreparedLocalProxyBackend('https://notice.invalid',proxy)


def test_different_origin_is_rejected_before_connecting():
    async def scenario():
        backend=PreparedLocalProxyBackend('https://notice.invalid','http://127.0.0.1:1')
        with pytest.raises(DispatchDenied,match='destination_changed'):
            await backend.prepare(httpx.URL('https://different.invalid/'))
    asyncio.run(scenario())
