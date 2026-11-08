"""Wire encoding tests use only a synchronous in-memory transport writer."""
import httpx
import pytest

from server.cloud_sync.wechat_dispatch_authority import DispatchDenied
from server.cloud_sync.wechat_prepared_connection import PreparedBackend, PreparedConnection


class Writer:
    def __init__(self):
        self.writes = []
        self.closed = False
    def write(self, data): self.writes.append(data)
    def is_closing(self): return self.closed


def test_one_synchronous_write_contains_the_complete_immutable_request():
    writer = Writer()
    request = httpx.Request('POST', 'https://example.invalid/worker/wechat-publish',
                            headers={'X-Eric-Worker-Key': 'synthetic'}, json={'synthetic': True})
    connection = PreparedConnection(request.url, None, writer)
    connection.accept(request)
    assert len(writer.writes) == 1
    wire = writer.writes[0]
    assert wire.startswith(b'POST /worker/wechat-publish HTTP/1.1\r\n')
    assert b'X-Eric-Worker-Key: synthetic\r\n' in wire
    assert wire.split(b'\r\n\r\n', 1)[1] == request.content
    with pytest.raises(DispatchDenied, match='prepared_request_mismatch'):
        connection.accept(request)
    assert len(writer.writes) == 1


def test_closed_connection_never_enqueues_any_bytes():
    writer = Writer()
    writer.closed = True
    request = httpx.Request('POST', 'https://example.invalid/', json={})
    with pytest.raises(DispatchDenied, match='prepared_connection_unavailable'):
        PreparedConnection(request.url, None, writer).accept(request)
    assert writer.writes == []


def test_plain_http_requires_explicit_loopback_test_mode():
    with pytest.raises(ValueError, match='verified_tls_required'):
        PreparedBackend('http://example.invalid/', loopback_http=True)
    with pytest.raises(ValueError, match='verified_tls_required'):
        PreparedBackend('http://127.0.0.1/')
    PreparedBackend('http://127.0.0.1/', loopback_http=True)
    with pytest.raises(ValueError, match='immutable_ca_required'):
        PreparedBackend('https://example.invalid/', ca_pem=object())
