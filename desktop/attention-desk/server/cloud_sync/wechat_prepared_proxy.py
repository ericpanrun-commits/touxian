"""Prepare verified origin TLS through an explicitly pinned local HTTP proxy.

CONNECT carries only the fixed origin authority, never an application header or
body. All preparation finishes before the existing protected byte handoff. No
proxy discovery, credentials, redirects, pooling or automatic retries occur.
"""
import asyncio
import re
import ssl

import httpx

from .wechat_dispatch_authority import DispatchDenied
from .wechat_prepared_connection import PreparedBackend,PreparedConnection


class PreparedLocalProxyBackend(PreparedBackend):
    def __init__(self,origin,proxy,*,ca_pem=None):
        super().__init__(origin,ca_pem=ca_pem)
        value=httpx.URL(proxy)
        if (value.scheme!='http' or value.host!='127.0.0.1' or value.userinfo
                or value.path!='/' or value.query or value.fragment or not value.port
                or not re.fullmatch(r'[a-z0-9.-]+',self.origin.host,re.I)):
            raise ValueError('explicit_local_http_proxy_required')
        self.proxy=value

    async def prepare(self,url):
        url=httpx.URL(url)
        if (url.scheme,url.host,url.port)!=(self.origin.scheme,self.origin.host,self.origin.port):
            raise DispatchDenied('destination_changed')
        writer=None;ready=False
        try:
            async with asyncio.timeout(10):
                reader,writer=await asyncio.open_connection(self.proxy.host,self.proxy.port,limit=8192)
                authority=f'{self.origin.host}:{self.origin.port or 443}'.encode('ascii')
                writer.write(b'CONNECT '+authority+b' HTTP/1.1\r\nHost: '+authority+b'\r\n\r\n')
                await writer.drain()
                response=await reader.readuntil(b'\r\n\r\n')
                if (len(response)>8192 or not re.fullmatch(rb'HTTP/1\.[01] 200(?: [^\r\n]*)?',response.split(b'\r\n',1)[0])
                        or reader._buffer):
                    raise httpx.ConnectError('proxy_tunnel_rejected')
                context=ssl.create_default_context(cadata=self.ca_pem)
                await writer.start_tls(context,server_hostname=self.origin.host,ssl_handshake_timeout=5,ssl_shutdown_timeout=1)
                ready=True
                return PreparedConnection(url,reader,writer)
        except (OSError,TimeoutError,asyncio.IncompleteReadError,asyncio.LimitOverrunError,ValueError) as error:
            raise httpx.ConnectError('proxy_connection_unavailable') from error
        finally:
            if writer is not None and not ready:
                writer.close()
                try:await asyncio.wait_for(writer.wait_closed(),timeout=1)
                except (OSError,TimeoutError):pass
