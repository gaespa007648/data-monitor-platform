"""Bound request bodies before JSON or multipart parsers allocate resources."""
from starlette.responses import JSONResponse


class RequestBodyLimit:
    def __init__(self, app, maximum=2 * 1024 * 1024 + 64 * 1024):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            too_large = int(headers.get(b"content-length", b"0")) > self.maximum
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
        body = bytearray()
        while not too_large:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.maximum:
                too_large = True
                break
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        if too_large:
            return await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
        sent = False
        async def bounded_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, bounded_receive, send)
