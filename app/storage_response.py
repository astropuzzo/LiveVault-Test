"""Range-capable media responses that release file handles before storage ack."""
import asyncio
import os
import secrets
import stat

import anyio
from starlette.datastructures import MutableHeaders
from starlette.responses import FileResponse, Response

from . import storage_handoff


class StorageFileResponse(FileResponse):
    def __init__(self, *args, expected_identity: tuple[int, int] | None = None, **kwargs):
        self.expected_identity = expected_identity
        self._pinned_file = None
        super().__init__(*args, **kwargs)

    async def _respond(self, scope, receive, send):
        if self.expected_identity is None:
            return await super().__call__(scope, receive, send)
        def open_pinned(path, flags):
            # Refuse a replacement symlink and avoid blocking on a replacement FIFO.
            return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            file = await anyio.open_file(self.path, mode="rb", opener=open_pinned)
        except OSError:
            return await Response(status_code=404)(scope, receive, send)
        async with file:
            actual = await anyio.to_thread.run_sync(os.fstat, file.fileno())
            if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != self.expected_identity:
                return await Response(status_code=404)(scope, receive, send)
            # FileResponse must use this open inode for both headers and bytes.
            # A second pathname open would recreate the validation/rename race.
            self.stat_result = actual
            for header in ("content-length", "last-modified", "etag"):
                if header in self.headers:
                    del self.headers[header]
            self.set_stat_headers(actual)
            self._pinned_file = file
            try:
                return await super().__call__(scope, receive, send)
            finally:
                self._pinned_file = None

    async def _send_pinned_range(self, send, start, end, *, final=True):
        await self._pinned_file.seek(start)
        remaining = end - start
        if not remaining:
            await send({"type": "http.response.body", "body": b"", "more_body": not final})
        while remaining:
            chunk = await self._pinned_file.read(min(self.chunk_size, remaining))
            if not chunk:
                raise RuntimeError("Pinned media file truncated during response")
            remaining -= len(chunk)
            await send({"type": "http.response.body", "body": chunk, "more_body": remaining > 0 or not final})

    async def _handle_simple(self, send, send_header_only, send_pathsend):
        if self._pinned_file is None:
            return await super()._handle_simple(send, send_header_only, send_pathsend)
        await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            await self._send_pinned_range(send, 0, self.stat_result.st_size)

    async def _handle_single_range(self, send, start, end, file_size, send_header_only):
        if self._pinned_file is None:
            return await super()._handle_single_range(send, start, end, file_size, send_header_only)
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-range"] = f"bytes {start}-{end - 1}/{file_size}"
        headers["content-length"] = str(end - start)
        await send({"type": "http.response.start", "status": 206, "headers": headers.raw})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            await self._send_pinned_range(send, start, end)

    async def _handle_multiple_ranges(self, send, ranges, file_size, send_header_only):
        if self._pinned_file is None:
            return await super()._handle_multiple_ranges(send, ranges, file_size, send_header_only)
        boundary = secrets.token_hex(13)
        length, generate_header = self.generate_multipart(ranges, boundary, file_size, self.headers["content-type"])
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-type"] = f"multipart/byteranges; boundary={boundary}"
        headers["content-length"] = str(length)
        await send({"type": "http.response.start", "status": 206, "headers": headers.raw})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        for start, end in ranges:
            await send({"type": "http.response.body", "body": generate_header(start, end), "more_body": True})
            await self._send_pinned_range(send, start, end, final=False)
            await send({"type": "http.response.body", "body": b"\r\n", "more_body": True})
        await send({"type": "http.response.body", "body": f"--{boundary}--".encode("latin-1"), "more_body": False})

    async def __call__(self, scope, receive, send):
        if not storage_handoff.media_online():
            return await Response(status_code=503, headers={"Retry-After": "5"})(scope, receive, send)
        # Do not hand ownership of the open file to an ASGI sendfile extension.
        scope = dict(scope)
        scope["extensions"] = {k: v for k, v in scope.get("extensions", {}).items()
                               if k != "http.response.pathsend"}
        storage_handoff.active_media_responses += 1
        task = asyncio.create_task(self._respond(scope, receive, send))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.25)
                if not storage_handoff.media_online() and not task.done():
                    # Also interrupt a slow client blocked in send(), rather than
                    # waiting for it to request/read another chunk indefinitely.
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise RuntimeError("Media response interrupted for storage handoff")
            await task
        finally:
            if not task.done():
                task.cancel()
            # FileResponse's async file context exits before the ack counter drops.
            await asyncio.gather(task, return_exceptions=True)
            storage_handoff.active_media_responses -= 1
