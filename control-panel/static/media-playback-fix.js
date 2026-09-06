(() => {
  if (window.__openastroHlsV15 || !window.Hls) return;
  window.__openastroHlsV15 = true;

  const BaseHls = window.Hls;
  class OpenAstroHls extends BaseHls {
    constructor(config = {}) {
      super({
        ...config,
        // Reliability is more important than a small client-side CPU saving.
        // Keeping transmuxing on the main thread also avoids worker/CSP/browser
        // edge cases seen with authenticated HLS segment URLs.
        enableWorker: false,
        lowLatencyMode: false,
        manifestLoadingMaxRetry: 4,
        manifestLoadingRetryDelay: 500,
        levelLoadingMaxRetry: 4,
        fragLoadingMaxRetry: 6,
        fragLoadingRetryDelay: 500,
        maxBufferLength: Math.max(45, Number(config.maxBufferLength || 0)),
        maxMaxBufferLength: 120,
        backBufferLength: Math.max(60, Number(config.backBufferLength || 0)),
      });

      this.__oaNetworkRecoveries = 0;
      this.__oaMediaRecoveries = 0;

      const note = text => {
        const target = document.querySelector('#mediaPlayerNote');
        if (target && text) target.textContent = text;
      };

      this.on(BaseHls.Events.MANIFEST_PARSED, () => {
        note('Stream HLS pronto · trasporto MPEG-TS compatibile.');
        const media = this.media;
        if (media && media.paused) media.play().catch(() => {});
      });

      this.on(BaseHls.Events.FRAG_BUFFERED, () => {
        this.__oaNetworkRecoveries = 0;
      });

      this.on(BaseHls.Events.ERROR, (_event, data) => {
        if (!data?.fatal) return;
        const detail = [data.type, data.details].filter(Boolean).join(' · ');
        if (data.type === BaseHls.ErrorTypes.NETWORK_ERROR && this.__oaNetworkRecoveries < 3) {
          this.__oaNetworkRecoveries += 1;
          note(`Riconnessione stream ${this.__oaNetworkRecoveries}/3…`);
          this.startLoad();
          return;
        }
        if (data.type === BaseHls.ErrorTypes.MEDIA_ERROR && this.__oaMediaRecoveries < 2) {
          this.__oaMediaRecoveries += 1;
          note(`Recupero decoder ${this.__oaMediaRecoveries}/2…`);
          this.recoverMediaError();
          return;
        }
        note(`Playback HLS interrotto${detail ? ` · ${detail}` : ''}. Chiudi e riapri il player per riprovare.`);
        try { this.destroy(); } catch (_) {}
      });
    }
  }

  window.Hls = OpenAstroHls;
})();
