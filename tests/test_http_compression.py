from fastapi import FastAPI
from fastapi.responses import Response
import asyncio

from app.http_compression import TextCompressionMiddleware


def test_compression_applies_to_text_and_preserves_media_ranges():
    app = FastAPI()
    app.add_middleware(TextCompressionMiddleware)

    @app.get("/static/example.js")
    def asset():
        return Response("// example\n" * 500, media_type="application/javascript")

    @app.get("/api/recordings/1/view")
    @app.get("/api/sources/1/capture")
    def media():
        return Response(b"media" * 500, status_code=206, headers={"Content-Range": "bytes 0-2499/5000"}, media_type="video/mp4")

    def get(path):
        messages = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            messages.append(message)
        scope = {"type":"http", "method":"GET", "path":path, "raw_path":path.encode(), "query_string":b"", "headers":[(b"accept-encoding",b"gzip")], "scheme":"http", "server":("test",80), "client":("test",1), "http_version":"1.1", "asgi":{"version":"3.0", "spec_version":"2.4"}}
        asyncio.run(app(scope, receive, send))
        headers = {key.decode():value.decode() for key,value in messages[0]["headers"]}
        body = b"".join(message.get("body", b"") for message in messages[1:])
        if headers.get("content-encoding") == "gzip":
            import gzip
            body = gzip.decompress(body)
        from types import SimpleNamespace
        return SimpleNamespace(headers=headers, status_code=messages[0]["status"], content=body, text=body.decode())
    asset = get("/static/example.js")
    assert asset.headers["content-encoding"] == "gzip"
    assert asset.text == "// example\n" * 500
    video = get("/api/recordings/1/view")
    assert video.status_code == 206
    assert "content-encoding" not in video.headers
    assert video.headers["content-range"] == "bytes 0-2499/5000"
    assert video.content == b"media" * 500
    capture = get("/api/sources/1/capture")
    assert capture.status_code == 206
    assert "content-encoding" not in capture.headers
    assert capture.headers["content-range"] == "bytes 0-2499/5000"
    assert capture.content == b"media" * 500
