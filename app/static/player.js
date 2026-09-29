/* Standalone player for a local capture opened as a page (/api/sources/N/capture, fragment/recording view).
   A fragmented MP4 that is still being written has no duration in its header, so the raw file has no
   usable timeline: play it through the server's HLS byte-range playlist instead. For a live capture the
   playlist grows, hls.js reloads it every few seconds and the timeline length follows the recording. */
(() => {
  const body = document.body;
  const video = document.getElementById('player');
  const length = document.getElementById('length');
  const edge = document.getElementById('edge');
  const message = document.getElementById('message');
  const playlist = body.dataset.playlist;
  const raw = body.dataset.raw;
  const live = body.dataset.live === '1';

  const clock = seconds => {
    const total = Math.max(0, Math.floor(seconds));
    const h = Math.floor(total / 3600);
    const m = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
    const s = String(total % 60).padStart(2, '0');
    return h ? `${h}:${m}:${s}` : `${m}:${s}`;
  };
  const recordedEnd = () => {
    if (video.seekable && video.seekable.length) return video.seekable.end(video.seekable.length - 1);
    return Number.isFinite(video.duration) ? video.duration : 0;
  };
  const fallback = reason => {
    message.textContent = reason;
    message.hidden = false;
    video.src = raw;
  };

  let hls = null;
  if (window.Hls && window.Hls.isSupported()) {
    // liveDurationInfinity stays false: the media duration is the recorded length, so the seek bar works.
    hls = new window.Hls({enableWorker: false, maxBufferLength: 30, backBufferLength: 60, liveDurationInfinity: false});
    hls.on(window.Hls.Events.ERROR, (_event, data) => {
      if (!data.fatal) return;
      hls.destroy();
      hls = null;
      fallback('Timeline non disponibile: riproduzione diretta del file.');
    });
    hls.loadSource(playlist);
    hls.attachMedia(video);
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = playlist;
  } else {
    fallback('Browser senza supporto HLS: riproduzione diretta del file.');
  }

  setInterval(() => {
    const end = recordedEnd();
    if (!end) return;
    length.textContent = live ? `Registrato finora: ${clock(end)}` : `Durata: ${clock(end)}`;
    edge.hidden = !live || end - video.currentTime < 15;
  }, 1000);
  edge.addEventListener('click', () => {
    video.currentTime = recordedEnd();
    video.play().catch(() => {});
  });
})();
