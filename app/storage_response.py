"""Range-capable media responses that release file handles before storage ack."""
import asyncio

from starlette.responses import FileResponse, Response

from . import storage_handoff


class StorageFileResponse(FileResponse):
    async def __call__(self, scope, receive, send):
        if not storage_handoff.media_online():
            return await Response(status_code=503, headers={"Retry-After": "5"})(scope, receive, send)
        # Do not hand ownership of the open file to an ASGI sendfile extension.
        scope = dict(scope)
        scope["extensions"] = {k: v for k, v in scope.get("extensions", {}).items()
                               if k != "http.response.pathsend"}
        storage_handoff.active_media_responses += 1
        task = asyncio.create_task(super().__call__(scope, receive, send))
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
