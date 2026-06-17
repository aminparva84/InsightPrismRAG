"""PrismRAG — Request ID propagation."""
from __future__ import annotations

import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

REQUEST_ID_HEADER = "X-Request-Id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request, call_next):
        trace_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = trace_id
        return response
