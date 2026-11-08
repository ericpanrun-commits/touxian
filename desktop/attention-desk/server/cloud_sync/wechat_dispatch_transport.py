"""HTTPX transport boundary for the isolated scoped sender.

Connection preparation precedes authorization/content locks. A backend must
return a prepared connection whose accept(request) synchronously hands off the
complete immutable request and whose response() waits for the response. There
is deliberately no fallback to an ordinary HTTP client or automatic retry.
No backend is installed in the running application by this module.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json
from uuid import uuid4

import httpx

from .wechat_dispatch_authority import DispatchDenied


def request_bytes(request):
    # Bind destination, credential, headers and body, not only message text.
    return json.dumps([request.method, str(request.url),
                       [(k.hex(), v.hex()) for k, v in request.headers.raw],
                       request.content.hex()], separators=(',', ':')).encode()


@dataclass
class _Permit:
    authority: object
    identity: str
    binding: str
    snapshot: object
    validate: object
    used: bool = False


class DispatchTransport(httpx.AsyncBaseTransport):
    def __init__(self, backend):
        self.backend = backend
        self._owner = object()

    async def post(self, client, url, *, authority, identity, binding,
                   snapshot, validate, **kwargs):
        if client._transport is not self:
            raise DispatchDenied('dispatch_transport_required')
        permit = _Permit(authority, identity, binding, snapshot, validate)
        return await client.post(url, extensions={'wechat_dispatch': (self._owner, permit)}, **kwargs)

    async def handle_async_request(self, request):
        value = request.extensions.get('wechat_dispatch')
        if (not isinstance(value, tuple) or len(value) != 2 or value[0] is not self._owner
                or type(value[1]) is not _Permit or value[1].used):
            raise DispatchDenied('dispatch_permit_required')
        permit = value[1]
        permit.used = True
        await request.aread()
        frozen = request_bytes(request)
        # Each invocation has its own one-use attempt. The existing sender's
        # journal must reconcile unknown delivery before permitting another.
        attempt = str(uuid4())
        permit.authority.reserve(permit.identity, permit.binding, attempt, frozen)
        connection = await self.backend.prepare(request.url)
        try:
            @contextmanager
            def protected():
                with permit.snapshot() as expected_body:
                    if request.content != expected_body:
                        raise DispatchDenied('content_changed')
                    if permit.validate() is not True:
                        raise DispatchDenied('dispatch_state_changed')
                    yield request_bytes(request)

            def accept(payload):
                if payload != request_bytes(request):
                    raise DispatchDenied('request_changed')
                # No await between final comparison and actual handoff. The
                # backend may enqueue immutable bytes but must not await I/O.
                connection.accept(request)

            permit.authority.handoff(permit.identity, permit.binding, attempt,
                                      frozen, protected_snapshot=protected, accept=accept)
            # No authorization or source lock is held while awaiting a response.
            return await connection.response()
        finally:
            await connection.aclose()

    async def aclose(self):
        await self.backend.aclose()
