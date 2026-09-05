"""Compress text responses without buffering recordings or preview images."""
from starlette.middleware.gzip import GZipMiddleware


class TextCompressionMiddleware:
    def __init__(self, app):
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=1000, compresslevel=4)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        media = path.endswith(("/view", "/download", "/thumbnail", "/preview", ".svg", ".png", ".jpg"))
        text = path in {"/", "/sw.js", "/manifest.webmanifest"} or path.startswith(("/static/", "/api/"))
        handler = self.compressed if scope["type"] == "http" and text and not media else self.app
        await handler(scope, receive, send)
