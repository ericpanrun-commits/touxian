"""One HTTP/1.1 request on a prepared connection; no pooling or retry.

Not enabled by the application. TCP/TLS is prepared before the caller takes
content/authorization locks. accept() gives one immutable encoded request to
asyncio's transport synchronously. That write, including transport buffering,
is the irreversible local handoff boundary. It does not prove cloud receipt.
"""
import asyncio
import ssl

import h11
import httpx

from .wechat_dispatch_authority import DispatchDenied


class PreparedBackend:
    def __init__(self, origin, *, ca_pem=None, loopback_http=False):
        self.origin = httpx.URL(origin)
        if self.origin.path != '/' or self.origin.query or self.origin.userinfo:
            raise ValueError('origin_required')
        if self.origin.scheme != 'https':
            if not (loopback_http is True and self.origin.scheme == 'http'
                    and self.origin.host in {'127.0.0.1', '::1'}):
                raise ValueError('verified_tls_required')
        if ca_pem is not None and type(ca_pem) is not str:
            raise ValueError('immutable_ca_required')
        self.ca_pem = ca_pem

    async def prepare(self, url):
        url = httpx.URL(url)
        if (url.scheme, url.host, url.port) != (self.origin.scheme, self.origin.host, self.origin.port):
            raise DispatchDenied('destination_changed')
        context = ssl.create_default_context(cadata=self.ca_pem) if url.scheme == 'https' else None
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(
                url.host, url.port or (443 if context else 80), ssl=context,
                server_hostname=url.host if context else None), timeout=5)
        except (OSError, TimeoutError) as error:
            raise httpx.ConnectError('connection_unavailable') from error
        return PreparedConnection(url, reader, writer)

    async def aclose(self):
        pass  # Each request owns and closes its one connection.


class PreparedConnection:
    def __init__(self, url, reader, writer):
        self.url, self.reader, self.writer = url, reader, writer
        self.protocol = h11.Connection(h11.CLIENT, max_incomplete_event_size=65536)
        self.accepted = False

    def accept(self, request):
        if self.accepted or request.method != 'POST' or request.url != self.url:
            raise DispatchDenied('prepared_request_mismatch')
        if self.writer.is_closing() or len(request.content) > 1024 * 1024:
            raise DispatchDenied('prepared_connection_unavailable')
        encoded = (self.protocol.send(h11.Request(method=b'POST', target=request.url.raw_path,
                    headers=request.headers.raw))
                   + self.protocol.send(h11.Data(data=request.content))
                   + self.protocol.send(h11.EndOfMessage()))
        self.accepted = True
        # Do not await drain here: revocation/content locks end after this
        # synchronous enqueue. A write error is conservatively outcome unknown.
        self.writer.write(encoded)

    async def response(self):
        if not self.accepted:
            raise DispatchDenied('request_not_handed_off')
        try:
            async with asyncio.timeout(10):
                await self.writer.drain()
                status, headers, body = None, [], bytearray()
                while True:
                    event = self.protocol.next_event()
                    if event is h11.NEED_DATA:
                        data = await self.reader.read(65536)
                        self.protocol.receive_data(data)
                    elif isinstance(event, h11.Response):
                        status, headers = event.status_code, event.headers
                    elif isinstance(event, h11.Data):
                        body.extend(event.data)
                        if len(body) > 1024 * 1024:
                            raise httpx.ReadError('response_too_large')
                    elif isinstance(event, h11.EndOfMessage):
                        if status is None:
                            raise httpx.ReadError('missing_response')
                        return httpx.Response(status, headers=headers, content=bytes(body))
                    elif isinstance(event, h11.ConnectionClosed):
                        raise httpx.ReadError('connection_closed')
        except (TimeoutError, OSError, h11.ProtocolError) as error:
            raise httpx.ReadError('response_unavailable') from error

    async def aclose(self):
        self.writer.close()
        try:
            await asyncio.wait_for(self.writer.wait_closed(), timeout=1)
        except (OSError, TimeoutError):
            pass
