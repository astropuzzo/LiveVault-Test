from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import signal
import sys
from pathlib import Path
from typing import Any

from curl_cffi import requests as curl_requests
from playwright.async_api import Page, async_playwright


WEBRTC_URL = "wss://edge-webrtc.doppiocdn.com/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)


def _broadcast(slug: str) -> dict[str, Any]:
    response = curl_requests.get(
        f"https://stripchat.com/api/front/v1/broadcasts/{slug}",
        headers={
            "Accept": "application/json",
            "Referer": f"https://stripchat.com/{slug}",
            "User-Agent": USER_AGENT,
        },
        timeout=20,
        impersonate="chrome",
    )
    response.raise_for_status()
    payload = response.json()
    item = payload.get("item") if isinstance(payload, dict) else None
    if (
        not isinstance(item, dict)
        or item.get("isLive") is not True
        or str(item.get("status") or "").lower() != "public"
        or not (item.get("streamName") or item.get("modelId"))
    ):
        raise RuntimeError("Stripchat stream is not a playable public broadcast")
    # mediaTransport describes how the performer publishes to Stripchat. RTMP
    # rooms are still distributed to viewers by the public WebRTC edge.
    item["streamName"] = str(item.get("streamName") or item["modelId"])
    return item


def _output_for(pattern: str, part: int) -> Path:
    return Path(pattern.replace("%03d", f"{part:03d}"))


async def _remux(raw: Path, output: Path, container: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.finalizing")
    temporary.unlink(missing_ok=True)
    common = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-fflags", "+genpts+discardcorrupt", "-i", str(raw),
        "-map", "0:v:0", "-map", "0:a:0", "-dn", "-ignore_unknown",
        "-avoid_negative_ts", "make_zero",
    ]
    trailer = ["-movflags", "+faststart"] if container == "mp4" else []

    async def run(codec_args: list[str]) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            *common, *codec_args, *trailer, str(temporary),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        return process.returncode, (stderr or b"").decode(errors="replace")[-1600:]

    suffix_format = ["-f", "mp4"] if container == "mp4" else ["-f", "matroska"]
    code, detail = await run(["-c", "copy", *suffix_format])
    if code != 0 and container == "mp4":
        temporary.unlink(missing_ok=True)
        code, detail = await run([
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-af", "aresample=async=1:first_pts=0", "-shortest",
            *suffix_format,
        ])
    if code != 0 or not temporary.is_file() or temporary.stat().st_size <= 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Stripchat segment finalization failed: {detail}")
    os.replace(temporary, output)
    raw.unlink(missing_ok=True)


CLIENT_HTML = r"""<!doctype html><meta charset="utf-8"><video id="v" autoplay muted playsinline></video><script>
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const uuid = () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
  const r = Math.floor(Math.random()*16), v = c === 'x' ? r : ((r&3)|8); return v.toString(16);
});
let stopping = false;
window.stopCapture = () => { stopping = true; };

window.capture = async args => {
  const session = uuid();
  const socket = new WebSocket(args.wsUrl);
  const peer = new RTCPeerConnection();
  let remote = null;
  let playing = false;
  let ended = false;
  let profiles = {};
  let selected = 'source';

  const open = new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, {once:true});
    socket.addEventListener('error', () => reject(new Error('Stripchat WebRTC socket failed')), {once:true});
  });
  socket.onopen = () => socket.send(JSON.stringify({message:'connection',data:[{
    appKey:args.appKey, clientBrowserVersion:navigator.userAgent,
    clientOSVersion:navigator.userAgent, mediaProviders:['WebRTC'],
    custom:{name:args.streamName,aclAuth:''}
  }]}));
  socket.onmessage = async event => {
    const msg = JSON.parse(event.data);
    if (msg.message === 'connected') {
      const cfg = msg.data?.peerConfig || {};
      peer.setConfiguration({iceServers:cfg.iceServers||[],iceTransportPolicy:cfg.iceTransportPolicy||'all'});
      socket.send(JSON.stringify({message:'getStreamInfo',data:{mediaSessionId:session,modelName:args.streamName}}));
    } else if (msg.message === 'getStreamInfoReply') {
      profiles = msg.data?.profiles || {};
      const choices = Object.entries(profiles).filter(([name]) => !name.includes('blurred'))
        .sort((a,b) => ((a[1].width||0)*(a[1].height||0))-((b[1].width||0)*(b[1].height||0)));
      selected = profiles.source ? 'source' : (choices.at(-1)?.[0] || 'source');
      const start = choices[0]?.[0] || selected;
      peer.addTransceiver('audio',{direction:'recvonly'});
      peer.addTransceiver('video',{direction:'recvonly'});
      peer.ontrack = e => { remote = e.streams[0] || new MediaStream([e.track]); document.querySelector('#v').srcObject = remote; };
      const offer = await peer.createOffer(); await peer.setLocalDescription(offer);
      socket.send(JSON.stringify({message:'playStream',data:[{hasAudio:true,hasVideo:true,mediaProvider:'WebRTC',mediaSessionId:session,name:args.streamName,published:false,quality:start,record:false,sdp:offer.sdp}]}));
    } else if (msg.message === 'setRemoteSDP' && msg.data?.[1]) {
      await peer.setRemoteDescription({type:'answer',sdp:msg.data[1]});
    } else if (msg.message === 'notifyStreamStatusEvent') {
      const status = msg.data?.[0]?.status;
      if (status === 'PLAYING') playing = true;
      if (['FINISHED','STOPPED','ERRORED','ENDED','DISCONNECTED','CLOSED'].includes(status)) ended = true;
    }
  };
  socket.onclose = () => { if (playing) ended = true; };
  await open;

  const deadline = Date.now()+45000;
  while ((!remote || !remote.getVideoTracks()[0] || !playing) && Date.now()<deadline && !stopping) await sleep(100);
  if (!remote || !remote.getVideoTracks()[0] || !playing) throw new Error('Stripchat WebRTC media did not start');
  socket.send(JSON.stringify({message:'changeQuality',data:{mediaSessionId:session,quality:selected}}));
  await sleep(1800);

  const types = ['video/mp4;codecs=avc1.42E01E,mp4a.40.2','video/mp4','video/webm;codecs=vp9,opus','video/webm;codecs=vp8,opus','video/webm'];
  const mime = types.find(type => MediaRecorder.isTypeSupported(type)) || '';
  const previewLoop = async () => {
    let first = true;
    while (!stopping && !ended) {
      try {
        const chunks = [];
        const recorder = new MediaRecorder(remote, mime ? {mimeType:mime,videoBitsPerSecond:1200000,audioBitsPerSecond:96000} : undefined);
        recorder.ondataavailable = event => { if (event.data?.size) chunks.push(event.data); };
        const stopped = new Promise(resolve => recorder.onstop = resolve);
        recorder.start(1000);
        const deadline = Date.now() + (first ? 4000 : 10000);
        while (!stopping && !ended && Date.now() < deadline) await sleep(200);
        recorder.stop(); await stopped;
        if (chunks.length) {
          const data = new Uint8Array(await new Blob(chunks,{type:mime}).arrayBuffer());
          let binary = ''; for (let i=0;i<data.length;i+=32768) binary += String.fromCharCode(...data.subarray(i,i+32768));
          await window.writeVideoPreview(btoa(binary),mime);
        }
        first = false;
      } catch (_) { await sleep(1000); }
    }
  };
  const previewTask = previewLoop();
  let part = 0;
  let total = 0;
  const video = document.querySelector('#v');
  let nextPreview = 0;

  while (!stopping && !ended) {
    part += 1;
    let index = 0, bytes = 0, chain = Promise.resolve();
    const recorder = new MediaRecorder(remote, mime ? {mimeType:mime,videoBitsPerSecond:3500000,audioBitsPerSecond:192000} : undefined);
    recorder.ondataavailable = event => {
      if (!event.data?.size) return;
      const myIndex = index++;
      bytes += event.data.size;
      chain = chain.then(async () => {
        const data = new Uint8Array(await event.data.arrayBuffer());
        let binary = ''; for (let i=0;i<data.length;i+=32768) binary += String.fromCharCode(...data.subarray(i,i+32768));
        await window.writeChunk(part,myIndex,btoa(binary),mime);
      });
    };
    const stopped = new Promise(resolve => recorder.onstop = resolve);
    recorder.start(1000);
    const started = Date.now();
    while (!stopping && !ended && Date.now()-started<args.segmentMs && bytes<args.maxBytes) {
      await sleep(250);
      if (video.videoWidth && video.videoHeight && Date.now() >= nextPreview) {
        nextPreview = Date.now() + 10000;
        const canvas=document.createElement('canvas'); canvas.width=640; canvas.height=Math.max(2,Math.round(640*video.videoHeight/video.videoWidth));
        canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
        await window.writePreview(canvas.toDataURL('image/jpeg',0.72).split(',')[1]);
      }
    }
    recorder.stop(); await stopped; await chain; await window.finishPart(part,mime);
    total += bytes;
  }
  await previewTask;
  try { socket.close(); } catch (_) {} try { peer.close(); } catch (_) {}
  return {parts:part,bytes:total,reason:stopping?'stopped':'stream_ended'};
};
</script>"""


async def capture(args: argparse.Namespace) -> None:
    info = await asyncio.to_thread(_broadcast, args.slug)
    raw_parts: dict[int, Path] = {}
    remux_tasks: list[asyncio.Task] = []
    stop_event = asyncio.Event()

    def request_stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):
            pass

    async with async_playwright() as playwright:
        executable = (
            os.getenv("CHROMIUM_PATH")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
            or playwright.chromium.executable_path
        )
        if not executable:
            raise RuntimeError("Chromium is not installed for Stripchat WebRTC capture")
        browser = await playwright.chromium.launch(
            executable_path=executable,
            headless=True,
            handle_sigint=False,
            handle_sigterm=False,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required"],
        )
        context = await browser.new_context(user_agent=USER_AGENT)
        page: Page = await context.new_page()

        async def write_chunk(part: int, _index: int, encoded: str, mime: str) -> int:
            capture_suffix = ".capture.mp4" if str(mime).lower().startswith("video/mp4") else ".capture.webm"
            path = raw_parts.setdefault(
                part,
                _output_for(args.output_pattern, part).with_suffix(capture_suffix),
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            data = base64.b64decode(encoded)
            with path.open("ab") as handle:
                handle.write(data)
            return len(data)

        async def finish_part(part: int, _mime: str) -> None:
            raw = raw_parts.get(part)
            if not raw or not raw.is_file() or raw.stat().st_size <= 0:
                return
            output = _output_for(args.output_pattern, part)
            remux_tasks.append(asyncio.create_task(_remux(raw, output, args.container)))

        async def write_preview(encoded: str) -> None:
            data = base64.b64decode(encoded)
            target = Path(args.preview)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp.jpg")
            temporary.write_bytes(data)
            os.replace(temporary, target)

        async def write_video_preview(encoded: str, mime: str) -> None:
            suffix = ".mp4" if str(mime).lower().startswith("video/mp4") else ".webm"
            base = Path(args.video_preview_base)
            target = base.with_suffix(suffix)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f"{target.name}.tmp")
            temporary.write_bytes(base64.b64decode(encoded))
            os.replace(temporary, target)
            for stale_suffix in ({".mp4", ".webm"} - {suffix}):
                base.with_suffix(stale_suffix).unlink(missing_ok=True)

        await page.expose_function("writeChunk", write_chunk)
        await page.expose_function("finishPart", finish_part)
        await page.expose_function("writePreview", write_preview)
        await page.expose_function("writeVideoPreview", write_video_preview)
        await page.set_content(CLIENT_HTML, wait_until="domcontentloaded")
        task = asyncio.create_task(page.evaluate(
            "args => window.capture(args)",
            {
                "streamName": str(info["streamName"]),
                "appKey": str(info.get("webRTCAppKey") or "callbackApp"),
                "wsUrl": WEBRTC_URL,
                "segmentMs": max(5, args.segment_seconds) * 1000,
                "maxBytes": max(64 * 1024 * 1024, args.max_bytes),
            },
        ))
        stop_wait = asyncio.create_task(stop_event.wait())
        done, _pending = await asyncio.wait({task, stop_wait}, return_when=asyncio.FIRST_COMPLETED)
        if stop_wait in done and not task.done():
            await page.evaluate("window.stopCapture()")
        result = await asyncio.wait_for(task, timeout=30)
        stop_wait.cancel()
        print(json.dumps(result), file=sys.stderr, flush=True)
        if remux_tasks:
            await asyncio.gather(*remux_tasks)
        await context.close()
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--output-pattern", required=True)
    parser.add_argument("--preview", required=True)
    parser.add_argument("--video-preview-base", required=True)
    parser.add_argument("--segment-seconds", required=True, type=int)
    parser.add_argument("--max-bytes", required=True, type=int)
    parser.add_argument("--container", choices=("mp4", "mkv"), required=True)
    args = parser.parse_args()
    asyncio.run(capture(args))


if __name__ == "__main__":
    main()
