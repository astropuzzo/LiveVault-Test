(() => {
  'use strict';
  const finite = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
  let snapshot = null;

  function report(data, now = Date.now() / 1000) {
    const host = data?.host || {}, power = data?.power || {}, lv = data?.livevault || {};
    const storage = data?.storage || {}, tasks = lv.worker?.tasks || {};
    const timestamp = finite(data?.timestamp);
    const age = timestamp == null ? null : Math.max(0, Math.floor(now - timestamp));
    const issues = [];
    const add = (id, severity, title, detail, route) => issues.push({id, severity, title, detail, route});
    if (age == null || age > 30) add('stale', 'warning', 'Dati da aggiornare', age == null ? 'Il nodo non ha ancora inviato un campione.' : `Ultimo campione ${age} secondi fa. Verifica la connessione.`, 'dashboard');
    if (lv.ok === false) add('livevault', 'critical', 'LiveVault richiede attenzione', 'Apri Avanzate per controllare servizi e container.', 'advanced');
    const stopped = Object.values(tasks).filter(value => value === false).length;
    if (stopped) add('workers', 'critical', `${stopped} processi LiveVault fermi`, 'Controlla lo stato dei worker in LiveVault prima di riavviare.', 'advanced');
    const mode = lv.storage_handoff?.mode;
    if (mode === 'quiesce') add('handoff', 'warning', 'Trasferimento storage in corso', 'Attendi il completamento prima di scollegare il disco.', 'storage');
    else if (mode === 'buffer') add('buffer', lv.storage_handoff?.full ? 'critical' : 'warning', lv.storage_handoff?.full ? 'Buffer pieno: registrazioni sospese' : 'Registrazione sul buffer interno', 'Ricollega NVMe e verifica il trasferimento in Storage.', 'storage');
    else if (storage.data?.mounted === false) add('nvme', 'warning', 'NVMe non montato', 'Verifica il collegamento e lo stato in Storage.', 'storage');
    for (const [key, title] of [['root', 'Memoria interna'], ['data', 'NVMe'], ['buffer', 'Buffer']]) {
      const disk = storage[key];
      if (disk?.mounted && finite(disk.percent) != null && disk.percent >= 90) add(`space-${key}`, disk.percent >= 97 ? 'critical' : 'warning', `${title}: spazio quasi esaurito`, `${disk.percent.toFixed(0)}% occupato. Controlla lo spazio disponibile.`, 'storage');
    }
    if (storage.share?.mounted === false) add('backup-storage', 'warning', 'Disco dei backup non disponibile', 'SHARE non è montato. I nuovi backup non possono essere salvati.', 'storage');
    if (power.undervoltage_now || power.throttled_now) add('power', 'critical', power.undervoltage_now ? 'Tensione di alimentazione bassa' : 'CPU limitata dal firmware', 'Verifica alimentazione e temperatura in Sistema.', 'system');
    else if (power.power_event_seen) add('power-history', 'info', 'Evento di alimentazione passato', 'Il firmware conserva un evento storico. Nessuna limitazione attuale segnalata.', 'system');
    if (finite(host.temperature) != null && host.temperature >= 72) add('temperature', host.temperature >= 80 ? 'critical' : 'warning', 'Temperatura elevata', `${host.temperature.toFixed(1)} °C. Controlla ventilazione e carico.`, 'system');
    if (finite(host.memory?.percent) != null && host.memory.percent >= 90) add('memory', 'warning', 'Memoria sotto pressione', `${host.memory.percent.toFixed(0)}% della RAM in uso. Controlla i container.`, 'advanced');
    for (const [key, title] of [['docker', 'Docker'], ['tailscale', 'Tailscale'], ['backup', 'Timer dei backup']]) {
      if (data?.services?.[key] && data.services[key] !== 'active') add(`service-${key}`, key === 'backup' ? 'warning' : 'critical', `${title} non attivo`, 'Controlla il servizio in Avanzate.', 'advanced');
    }
    const priority = {critical: 0, warning: 1, info: 2};
    issues.sort((a, b) => priority[a.severity] - priority[b.severity]);
    // Explicit allowlist: credentials, endpoints, DNS devices, filenames, paths,
    // raw worker errors and action logs never enter the downloadable report.
    return {
      schema: 1, generated_at: new Date(now * 1000).toISOString(), sampled_at: timestamp == null ? null : new Date(timestamp * 1000).toISOString(), age_seconds: age,
      versions: {control: String(data?.version || ''), livevault: String(lv.version || '')},
      host: {uptime_seconds: finite(host.uptime), cpu_percent: finite(host.cpu_percent), temperature_c: finite(host.temperature), memory_percent: finite(host.memory?.percent)},
      storage: Object.fromEntries(['root', 'data', 'share', 'buffer'].map(key => [key, {mounted: typeof storage[key]?.mounted === 'boolean' ? storage[key].mounted : null, percent: finite(storage[key]?.percent), free_bytes: finite(storage[key]?.free)}])),
      livevault: {ok: typeof lv.ok === 'boolean' ? lv.ok : null, active_recorders: finite(lv.worker?.active_recorders), stopped_workers: stopped, storage_mode: ['nvme','buffer','quiesce'].includes(mode) ? mode : 'unknown'},
      power: {undervoltage_now: power.undervoltage_now === true, throttled_now: power.throttled_now === true, historical_event: power.power_event_seen === true},
      issues,
    };
  }

  function render() {
    const list = document.getElementById('diagnosticIssues');
    if (!list) return;
    const result = report(snapshot);
    const count = result.issues.filter(issue => issue.severity !== 'info').length;
    const summary = document.getElementById('diagnosticSummary');
    summary.textContent = count ? `${count} ${count === 1 ? 'segnalazione' : 'segnalazioni'}` : snapshot ? 'Nessuna criticità segnalata' : 'Verifica in corso';
    summary.className = `status-chip ${count ? 'warn' : snapshot ? 'good' : ''}`;
    list.replaceChildren();
    for (const issue of result.issues) {
      const link = document.createElement('a');
      link.href = `#${issue.route}`;
      link.className = `diagnostic-issue ${issue.severity}`;
      const title = document.createElement('strong'), detail = document.createElement('small');
      title.textContent = issue.title; detail.textContent = issue.detail;
      link.append(title, detail); list.append(link);
    }
    if (!result.issues.length) {
      const text = document.createElement('p');
      text.className = 'muted'; text.textContent = 'Servizi, spazio, temperatura e alimentazione nei limiti osservati.';
      list.append(text);
    }
    document.getElementById('diagnosticAge').textContent = result.age_seconds == null ? 'In attesa del nodo' : `Campione di ${result.age_seconds} s fa`;
    document.getElementById('exportDiagnostics').disabled = !snapshot;
  }

  globalThis.OpenAstroDiagnostics = {report, update(data) { snapshot = data; render(); }};
  if (typeof document === 'undefined') return;
  document.getElementById('exportDiagnostics')?.addEventListener('click', () => {
    if (!snapshot) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(report(snapshot), null, 2)], {type: 'application/json'}));
    const link = document.createElement('a'); link.href = url;
    link.download = `openastro-diagnostics-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) render(); });
  setInterval(() => { if (!document.hidden && snapshot) render(); }, 5000);
})();
