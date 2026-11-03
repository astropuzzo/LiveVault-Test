(() => {
  'use strict';
  // Biglie's Oracle slot hunt in the App view: status and log while the view is on screen, controls and phone notices.
  const q = selector => document.querySelector(selector);
  const POLL_MS = 10000;
  // The server can spend up to 8 s reading status and another 8 s reading the log.
  const READ_TIMEOUT_MS = 20000;
  const CHIPS = {searching:['In cerca',''], running:['Macchina trovata','good'], growing:['Macchina trovata','good'], top:['Taglia massima','good'],
    error:['Errore','bad'], paused:['In pausa','warn'], stopped:['Ferma','']};
  const LABELS = {searching:'in cerca di posto', running:'macchina attiva', growing:'macchina attiva, cerca più spazio', top:'taglia massima raggiunta',
    error:'errore', paused:'in pausa', stopped:'fermata', unknown:'sconosciuto'};
  // Seconds between the hunt's rounds that hunt.sh interval accepts.
  const CHOICES = [15, 30, 60, 90];
  const WAIT_NOTE = 'Attesa tra i giri. Le richieste di creazione partono almeno 2 minuti dopo l’ultima risposta di Oracle; dopo errori ripetuti l’attesa cresce fino a 10 minuti.';
  let view = 'dashboard', authenticated = true, controller = null, generation = 0, busy = false, data = null, pushSynced = false;

  const setText = (selector, value) => { const node = q(selector); if (node) node.textContent = value; };
  const choice = seconds => q(`#biglieHuntInterval [data-seconds="${seconds}"]`);
  const token = () => typeof csrf === 'string' ? csrf : '';
  const say = (message, error = false) => { if (typeof toast === 'function') toast(message, error); };
  const sentence = text => /[.!?]$/.test(text) ? text : `${text}.`;
  const canLoad = () => authenticated && view === 'apps' && !document.hidden;

  function when(epoch) {
    if (!Number.isFinite(epoch)) return 'Mai';
    const date = new Date(epoch * 1000), now = new Date();
    const minutes = Math.max(0, Math.floor((now - date) / 60000));
    const sameDay = date.toDateString() === now.toDateString();
    const clock = date.toLocaleString('it-IT', sameDay ? {hour:'2-digit', minute:'2-digit'} : {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});
    const hours = Math.floor(minutes / 60), days = Math.floor(minutes / 1440);
    const ago = minutes < 1 ? 'adesso' : minutes < 60 ? `${minutes} min fa` : hours < 24 ? `${hours} ${hours === 1 ? 'ora' : 'ore'} fa` : `${days} ${days === 1 ? 'giorno' : 'giorni'} fa`;
    return `${clock}, ${ago}`;
  }

  function describe(d) {
    const m = d.machine, size = m ? `${m.ocpus} OCPU e ${m.gb} GB` : '', ip = m?.ip ? `, IP ${m.ip}` : '';
    if (!d.ok && d.installed) return [['Non leggibile','bad'], `Lo stato della ricerca non si legge: ${sentence(d.error || 'errore senza dettagli')} Nuovo controllo tra 10 secondi.`];
    if (!d.ok) return [['Non raggiungibile','bad'], `Il pannello non risponde: ${sentence(d.error || 'errore senza dettagli')} Nuovo controllo tra 10 secondi.`];
    if (!d.installed) return [['Non installata',''], 'Il pannello non vede la cartella /home/astro/biglie-oci: la cartella manca oppure il servizio del pannello non la monta.'];
    if (!d.alive) {
      if (!d.state) return [['Mai avviata',''], 'La ricerca non è mai partita su questo nodo. Premi «Avvia ricerca».'];
      if (d.state === 'top') return [['Finita','good'], `La macchina Arm ha la taglia massima gratuita: ${size}${ip}. La ricerca è finita.`];
      if (d.state === 'stopped') return [['Ferma',''], 'La ricerca è ferma. Premi «Avvia ricerca» per farla ripartire.'];
      return [['Processo fermo','bad'], `Il processo non è attivo (ultimo stato: ${LABELS[d.state] || 'sconosciuto'}). Dopo un riavvio dell’ASIAIR non riparte da solo: premi «Avvia ricerca».`];
    }
    if (d.paused && d.state !== 'paused') return [['Pausa richiesta','warn'], 'Pausa richiesta: la ricerca si ferma entro 5 secondi.'];
    if (!d.state || d.state === 'stopped') return [['In avvio',''], 'Il processo è partito: lo stato arriva alla fine del primo giro.'];
    const chip = CHIPS[d.state] || ['Sconosciuto','warn'];
    switch (d.state) {
      case 'searching': return [chip, 'Oracle non ha ancora posto per la macchina Arm. La ricerca riprova da sola a ogni giro.'];
      case 'running': return [chip, `Macchina Arm attiva: ${size}${ip}. La ricerca prova a farla crescere.`];
      case 'growing': return [chip, `Macchina Arm attiva: ${size}${ip}. Oracle non ha spazio per una taglia più grande, la ricerca riprova da sola.`];
      case 'top': return [chip, `Macchina Arm alla taglia massima gratuita: ${size}${ip}. La ricerca è finita.`];
      case 'error': {
        if ((d.reason === 'rate_limited' && d.http_status === 429) || /HTTP\s+429|TooManyRequests/.test(d.error || '')) {
          const wait = /Nuovo tentativo tra (\d+) s\./.exec(d.error || '');
          const nextAt = Number.isFinite(d.next_retry_at) ? d.next_retry_at : wait && Number.isFinite(d.updated) ? d.updated + Number(wait[1]) : null;
          const next = Number.isFinite(nextAt) ? ` Prossimo tentativo previsto alle ${new Date(nextAt * 1000).toLocaleTimeString('it-IT')}.` : '';
          return [['In attesa', 'warn'], `Oracle chiede di rallentare (HTTP 429). La ricerca è attiva e riprova automaticamente.${next}`];
        }
        return [chip, `Ultimo errore: ${sentence(d.error || 'senza dettagli')} La ricerca riprova da sola.`];
      }
      case 'paused': return [chip, `In pausa: nessun tentativo finché non premi «Riprendi».${m ? ` La macchina Arm resta attiva: ${size}${ip}.` : ''}`];
      default: return [chip, 'Stato non riconosciuto: controlla il registro qui sotto.'];
    }
  }

  function setButtons() {
    const installed = Boolean(data?.ok && data.installed && !data.read_stale), alive = installed && data.alive === true;
    const pause = q('#biglieHuntPause');
    if (pause) { pause.textContent = data?.paused ? 'Riprendi' : 'Pausa'; pause.disabled = busy || !installed; }
    for (const [selector, off] of [['#biglieHuntStop', !alive], ['#biglieHuntRestart', !alive], ['#biglieHuntStart', !installed || alive]]) {
      const button = q(selector); if (button) button.disabled = busy || off;
    }
    // An older hunt.sh reports no interval: the choice stays off until the script is updated.
    const current = installed ? data.interval : null, known = Number.isInteger(current);
    for (const seconds of CHOICES) {
      const button = choice(seconds); if (!button) continue;
      button.className = seconds === current ? 'active' : '';
      button.setAttribute('aria-pressed', String(seconds === current));
      button.disabled = busy || !known;
    }
    setText('#biglieHuntIntervalNote', installed && !known ? 'Lo script della ricerca su questo nodo non sa ancora cambiare l’attesa: va aggiornato dal repository Biglie.' : WAIT_NOTE);
  }

  function setFacts(values) {
    for (const [selector, value] of Object.entries(values)) setText(selector, value);
  }

  function render(d) {
    data = d;
    const [[label, tone], text] = describe(d);
    setText('#biglieHuntChip', label);
    const chip = q('#biglieHuntChip'); if (chip) chip.className = `status-chip ${tone}`.trim();
    setText('#biglieHuntSummary', text);
    if (d.ok && d.installed) {
      setFacts({'#biglieHuntProcess': d.alive ? 'Attivo' : 'Fermo', '#biglieHuntRound': d.round > 0 ? String(d.round) : 'Nessuno',
        '#biglieHuntUpdated': when(d.updated), '#biglieHuntMachine': d.machine ? `${d.machine.ocpus} OCPU · ${d.machine.gb} GB` : 'Non ancora',
        '#biglieHuntIp': d.machine?.ip || 'Non ancora'});
      setText('#biglieHuntLog', d.log?.length ? d.log.join('\n') : d.log_ok === false ? 'Registro non leggibile.' : 'Il registro è vuoto.');
    } else {
      const none = 'Non disponibile';
      setFacts({'#biglieHuntProcess': none, '#biglieHuntRound': none, '#biglieHuntUpdated': none, '#biglieHuntMachine': none, '#biglieHuntIp': none});
      setText('#biglieHuntLog', 'Registro non disponibile.');
    }
    setButtons();
  }

  function clearPrivate(message) {
    data = null;
    setText('#biglieHuntChip', 'Da verificare');
    const chip = q('#biglieHuntChip'); if (chip) chip.className = 'status-chip';
    setText('#biglieHuntSummary', message);
    setFacts({'#biglieHuntProcess': '…', '#biglieHuntRound': '…', '#biglieHuntUpdated': '…', '#biglieHuntMachine': '…', '#biglieHuntIp': '…'});
    setText('#biglieHuntLog', 'In attesa del registro…');
    setButtons();
  }

  function readFailure(error) {
    if (!data?.ok || !data.installed) return render({ok:false, error});
    data = {...data, read_stale:true};
    setText('#biglieHuntChip', 'Lettura in ritardo');
    const chip = q('#biglieHuntChip'); if (chip) chip.className = 'status-chip warn';
    setText('#biglieHuntSummary', `Il pannello non ha aggiornato lo stato: ${sentence(error)} Restano visibili gli ultimi dati ricevuti; nuovo controllo tra 10 secondi. Questo avviso non indica un arresto della ricerca Oracle.`);
    setButtons();
  }

  function stopRequest() {
    generation++; controller?.abort(); controller = null;
    q('#biglieHunt')?.setAttribute('aria-busy', 'false');
  }

  async function refresh() {
    if (!canLoad() || controller) return;
    const request = ++generation;
    controller = new AbortController(); const active = controller;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; active.abort(); }, READ_TIMEOUT_MS);
    q('#biglieHunt')?.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch('/api/biglie/hunt', {cache:'no-store', signal:active.signal});
      if (request !== generation || !canLoad()) return;
      if (response.status === 401) { if (typeof showLogin === 'function') showLogin(); else setAuthenticated(false); return; }
      const body = await response.json();
      if (request !== generation || !canLoad()) return;
      if (response.ok && body.ok) render(body);
      else readFailure(body.error || `HTTP ${response.status}`);
    } catch (error) {
      if (request !== generation || (error.name === 'AbortError' && !timedOut)) return;
      readFailure(timedOut ? 'nessuna risposta in 20 secondi' : 'connessione assente');
    } finally {
      clearTimeout(timer);
      if (request === generation) { controller = null; q('#biglieHunt')?.setAttribute('aria-busy', 'false'); }
    }
  }

  async function post(url, body, timeout = 20000) {
    const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':token()}, body:JSON.stringify(body), signal:AbortSignal.timeout(timeout)});
    if (response.status === 401) { if (typeof showLogin === 'function') showLogin(); throw new Error('Sessione scaduta: accedi di nuovo.'); }
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.ok === false) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  }

  async function act(action, extra = {}) {
    if (busy || !authenticated) return;
    busy = true; setButtons();
    try {
      const result = await post('/api/biglie/hunt', {action, ...extra}, 45000);
      say(result.message || 'Fatto.');
    } catch (error) {
      say(error.name === 'TimeoutError' || error.name === 'AbortError' ? 'Il pannello non ha risposto: controlla lo stato prima di riprovare.' : error.message, true);
    } finally {
      busy = false; setButtons();
      stopRequest(); refresh();
      // The hunt writes its pid and first status a moment after start: read once more.
      setTimeout(() => { if (canLoad()) refresh(); }, 3000);
    }
  }

  // Phone notices: the same Web Push the panel's service worker shows, one subscription per device.
  const pushSupported = () => typeof navigator !== 'undefined' && 'serviceWorker' in navigator && typeof window !== 'undefined' && 'PushManager' in window && 'Notification' in window;
  function serverKey(text) {
    const raw = atob((text + '='.repeat((4 - text.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, char => char.charCodeAt(0));
  }
  async function worker() {
    await navigator.serviceWorker.register('/sw.js');
    return Promise.race([navigator.serviceWorker.ready, new Promise((_, reject) => setTimeout(() => reject(new Error('il service worker del pannello non è pronto')), 8000))]);
  }
  function showPush(text, on, test, off) {
    setText('#biglieHuntPushState', text);
    for (const [selector, visible] of [['#biglieHuntPushOn', on], ['#biglieHuntPushTest', test], ['#biglieHuntPushOff', off]]) {
      const button = q(selector); if (button) button.hidden = !visible;
    }
  }
  async function renderPush() {
    if (!pushSupported()) {
      const ios = /iPad|iPhone|iPod/.test(typeof navigator === 'undefined' ? '' : navigator.userAgent || '');
      return showPush(ios ? 'Su iPhone aggiungi il pannello alla schermata Home (Condividi, poi Aggiungi alla schermata Home) e aprilo da lì.' : 'Questo browser non riceve notifiche dal pannello.', false, false, false);
    }
    if (Notification.permission === 'denied') return showPush('Notifiche bloccate: consentile per questo sito nelle impostazioni del browser.', false, false, false);
    try {
      const subscription = await (await worker()).pushManager.getSubscription();
      // Once per page load, so the server keeps this device even if its list was lost.
      if (subscription && !pushSynced && token()) { await post('/api/push/subscribe', subscription.toJSON()); pushSynced = true; }
      showPush(subscription ? 'Avvisi attivi su questo dispositivo.' : 'Avvisi spenti su questo dispositivo.', !subscription, Boolean(subscription), Boolean(subscription));
    } catch (error) {
      showPush(`Stato delle notifiche non disponibile: ${error.message}.`, true, false, false);
    }
  }
  async function pushStep(button, step) {
    if (button.disabled) return;
    button.disabled = true;
    try { await step(); } catch (error) { say(error.message, true); } finally { button.disabled = false; await renderPush(); }
  }
  async function pushOn() {
    if (await Notification.requestPermission() !== 'granted') return;
    const registration = await worker();
    const response = await fetch('/api/push/key', {cache:'no-store', signal:AbortSignal.timeout(10000)});
    const body = await response.json().catch(() => ({}));
    if (!response.ok || !body.key) throw new Error(body.error || 'Chiave delle notifiche non disponibile.');
    const old = await registration.pushManager.getSubscription();
    if (old) await old.unsubscribe();
    const subscription = await registration.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:serverKey(body.key)});
    await post('/api/push/subscribe', subscription.toJSON());
    pushSynced = true;
    say('Avvisi attivi su questo dispositivo.');
  }
  async function pushOff() {
    const subscription = await (await worker()).pushManager.getSubscription();
    if (subscription) { await post('/api/push/unsubscribe', {endpoint:subscription.endpoint}); await subscription.unsubscribe(); }
    say('Avvisi spenti su questo dispositivo.');
  }

  function setView(next) {
    view = next;
    if (!canLoad()) return stopRequest();
    refresh(); renderPush();
  }
  function setAuthenticated(value) {
    const next = Boolean(value);
    if (next === authenticated) return;
    authenticated = next;
    if (!authenticated) { stopRequest(); clearPrivate('Accedi per vedere la ricerca.'); }
    else if (canLoad()) { refresh(); renderPush(); }
  }
  globalThis.OpenAstroBiglie = {setView, setAuthenticated, refresh, describe};
  if (typeof document === 'undefined' || !q('#biglieHunt')) return;
  q('#biglieHuntPause').addEventListener('click', () => act(data?.paused ? 'resume' : 'pause'));
  q('#biglieHuntStop').addEventListener('click', () => act('stop'));
  q('#biglieHuntRestart').addEventListener('click', () => act('restart'));
  q('#biglieHuntStart').addEventListener('click', () => act('start'));
  for (const seconds of CHOICES) choice(seconds)?.addEventListener('click', () => { if (seconds !== data?.interval) act('interval', {seconds}); });
  q('#biglieHuntPushOn').addEventListener('click', event => pushStep(event.currentTarget, pushOn));
  q('#biglieHuntPushOff').addEventListener('click', event => pushStep(event.currentTarget, pushOff));
  q('#biglieHuntPushTest').addEventListener('click', event => pushStep(event.currentTarget, async () => say((await post('/api/push/test', {})).message)));
  q('#appsRefresh')?.addEventListener('click', () => { if (canLoad()) { stopRequest(); refresh(); } });
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopRequest(); else if (canLoad()) { refresh(); renderPush(); } });
  setInterval(() => { if (canLoad()) refresh(); }, POLL_MS);
})();
