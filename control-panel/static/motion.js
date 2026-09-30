/* OpenAstro motion — finite, event-driven choreography across the suite.
   Polling never replays effects. Reduced motion, hidden tabs and BFCache are handled. */
(() => {
  'use strict';
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)');
  const seen = new WeakSet(), active = new Set(), closing = new WeakMap(), byNode = new WeakMap(), detailsGestures = new WeakMap();
  const chartVisits = new Map(), listVisits = new Map();
  let observed = new WeakSet(), forced = new WeakSet();
  let queued = false, stopped = false, lastKey = '', gesture = 0, lastGesture = 0, intentKind = '';
  const ease = 'cubic-bezier(.22,1,.36,1)';
  const roots = [...['app', 'appView', 'login', 'loginView', 'loginGate', 'dashboard']
    .map(id => document.getElementById(id)).filter(Boolean),
    ...document.querySelectorAll('[data-page], [data-view-panel]')];
  const visible = node => node && !node.hidden && node.getClientRects().length > 0;
  const enabled = () => !stopped && !document.hidden && !reduced?.matches && typeof Element.prototype.animate === 'function';
  const key = () => `${location.pathname}|${location.hash}|${document.body.dataset.view || ''}|${roots.map(node => visible(node) ? '1' : '0').join('')}`;
  const inViewport = node => {
    const r = node.getBoundingClientRect?.();
    return !r || (r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth);
  };
  function play(node, frames, timing, finish = () => {}) {
    if (!enabled() || !visible(node)) { finish(); return null; }
    const previous = byNode.get(node);
    if (previous) { previous.cancel(); previous._openAstroFinish?.(); }
    if (active.size >= 80) { finish(); return null; }
    let complete = false;
    const animation = node.animate(frames, {easing: ease, fill: 'backwards', ...timing});
    const done = () => { if (complete) return; complete = true; active.delete(animation); if (byNode.get(node) === animation) byNode.delete(node); if (timing.fill === 'forwards') animation.cancel(); finish(); };
    animation._openAstroFinish = done;
    active.add(animation); byNode.set(node, animation);
    animation.finished.then(done, done);
    return animation;
  }
  function cancel() {
    active.forEach(animation => { animation.cancel(); animation._openAstroFinish?.(); });
    active.clear();
  }
  function enter(node, index = 0, force = false) {
    if (!enabled() || !visible(node) || (seen.has(node) && !force)) return;
    seen.add(node);
    play(node, [{opacity: .25, transform: 'translateY(16px) scale(.99)'}, {opacity: 1, transform: 'none'}],
      {duration: 560, delay: Math.min(index * 55, 220)});
  }
  function open(node) {
    const pending = closing.get(node);
    if (pending) { pending.abort(); closing.delete(node); }
    node?.classList?.add('motion-opening');
    play(node, [{opacity: .15, transform: 'translateY(18px) scale(.975)'}, {opacity: 1, transform: 'none'}],
      {duration: 380}, () => node?.classList?.remove('motion-opening'));
  }
  function close(node, finish) {
    if (closing.has(node)) return;
    if (!enabled() || !visible(node)) { finish(); return; }
    let aborted = false;
    node.classList?.add('motion-closing');
    const animation = play(node, [{opacity: 1, transform: 'none'}, {opacity: 0, transform: 'translateY(10px) scale(.985)'}],
      {duration: 180, fill: 'forwards'}, () => {
        closing.delete(node); node.classList?.remove('motion-closing');
        if (!aborted) finish();
      });
    // Reopening a surface cancels only its exit, preserving the new content.
    if (animation) closing.set(node, {abort() { aborted = true; animation?.cancel(); animation?._openAstroFinish?.(); }});
  }
  function press(node) {
    if (!node || node.disabled) return;
    play(node, [{transform:'scale(1)'}, {transform:'scale(.955)', offset:.32}, {transform:'scale(1)'}], {duration:280});
  }
  function draw(scope, force = false) {
    if (!enabled() || !scope?.querySelectorAll || !visible(scope) || !inViewport(scope)) return;
    const identity = scope.id || scope.dataset?.motionKey;
    const visit = chartVisits.get(identity);
    if (identity && visit?.key === lastKey && (!force || visit.gesture === gesture)) return;
    const paths = scope.querySelectorAll('.chart-line, .tl-q, .tl-c, .tl-g, .tl-s, .tl-b, .line-ra, .line-dec');
    const bars = scope.querySelectorAll('.chart-bar, .stellar-bar, .bar-column');
    if (!paths.length && !bars.length) return;
    if (identity) chartVisits.set(identity, {key:lastKey, gesture:force ? gesture : 0});
    Array.from(paths).slice(0,16).forEach((path,index) => {
      let length;
      try { length = path.getTotalLength(); } catch (_) { return; }
      if (!Number.isFinite(length) || length <= 0) return;
      // Animated values leave no inline dash styles to disturb future telemetry renders.
      play(path, [{strokeDasharray:`${length} ${length}`, strokeDashoffset:length, opacity:.2},
        {strokeDasharray:`${length} ${length}`, strokeDashoffset:0, opacity:1}],
        {duration:720,delay:Math.min(index*35,140)});
    });
    Array.from(bars).slice(0,60).forEach((bar,index) => {
      play(bar, [{opacity:.1, clipPath:'inset(100% 0 0 0)'}, {opacity:1,clipPath:'inset(0)'}],
        {duration:620,delay:Math.min(index*12,180)});
    });
  }
  function render(scope, kind = 'list') {
    if (!enabled() || !visible(scope)) return;
    const recent = gesture > 0 && Date.now() - lastGesture < 1600 && ['list','chart','selection','route'].includes(intentKind);
    const identity = scope.id || scope.dataset?.motionKey;
    if (kind === 'chart') { draw(scope, recent); return; }
    if (!recent || !identity || listVisits.get(identity) === gesture) return;
    listVisits.set(identity, gesture);
    if (kind === 'selection') {
      play(scope,[{opacity:.25,transform:'translateX(8px)'},{opacity:1,transform:'none'}],{duration:320});
      draw(scope,true); return;
    }
    const items = scope.querySelectorAll?.('.library-card, .rec-card, .cr-live-card, .cr-compact-row, .media-profile-card, .media-file, .media-file-v2');
    Array.from(items || []).filter(node => visible(node) && inViewport(node)).slice(0,16).forEach((node,index) =>
      play(node,[{opacity:.2,transform:'translateY(10px)'},{opacity:1,transform:'none'}],{duration:380,delay:Math.min(index*35,175)}));
  }
  const intersection = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
    entries.filter(entry => entry.isIntersecting).forEach((entry,index) => {
      if (!enabled()) return;
      enter(entry.target,index,forced.has(entry.target)); forced.delete(entry.target);
      draw(entry.target);
      intersection.unobserve(entry.target);
    });
  }, {threshold:.06}) : null;
  function reveal() {
    queued = false;
    if (!enabled()) { cancel(); return; }
    const nextKey = key();
    if (nextKey === lastKey) return;
    const revisit = Boolean(lastKey);
    lastKey = nextKey;
    Array.from(document.querySelectorAll('[data-motion]')).filter(visible).forEach((node,index) => {
      if (seen.has(node) && !revisit) return;
      if (revisit) forced.add(node);
      if (!intersection) { enter(node,index,revisit); draw(node); return; }
      if (!observed.has(node) || revisit) { observed.add(node); intersection.observe(node); }
    });
    if (!revisit) document.querySelectorAll('[data-orbit]').forEach((node,index) => {
      play(node,[{opacity:.15,transform:'scale(.82) rotate(-12deg)'},{opacity:1,transform:'none'}],{duration:900,delay:Math.min(index*80,160)});
    });
  }
  function schedule() {
    if (stopped || queued || document.hidden) return;
    queued = true; window.requestAnimationFrame(reveal);
  }
  function intent(event) {
    const target = event.target;
    if (!target?.closest || target.closest('[disabled], [aria-disabled="true"]')) return;
    const summary = target.closest('summary');
    if (summary?.parentElement) detailsGestures.set(summary.parentElement, Date.now());
    gesture++; lastGesture = Date.now();
    intentKind = target.closest('[data-frame-id], #stellarSelect') ? 'selection'
      : target.closest('[data-view], [data-route], .top-nav, .main-nav, .nina-nav') ? 'route'
      : target.closest('[data-range], #statisticsDays, #statisticsRefresh, #refreshButton') ? 'chart'
      : target.closest('input[type="search"], select, [data-media-filter], [data-library-smart], #libraryGridBtn, #libraryListBtn, #mediaGridView, #mediaListView, .archive-filter') ? 'list' : 'surface';
    window.requestAnimationFrame(() => {
      if (!enabled()) return;
      schedule();
      const scope = target.closest('[data-page], [data-view-panel], main');
      if (scope) {
        scope.querySelectorAll('[data-motion], [id]').forEach(node => {
          if (node.matches?.('#sources, #librarySources, #recordings, #mediaFiles, #mediaRecent, #mediaHomeResume, #mediaHomeFavorites')) render(node);
          if (['chart','selection','route'].includes(intentKind) && node.matches?.('#pluginTimeline, #guidePlot, #guideChart, #computeChart, #temperatureChart, #networkChart, #diskChart, #wattChart, #stellarPanel')) draw(node,true);
        });
      }
    });
  }
  window.OpenAstroMotion = {open, close, press, render, draw};
  document.addEventListener('pointerdown', event => {
    if (event.button != null && event.button !== 0) return;
    const node = event.target?.closest?.('button, a, summary, [role="button"]');
    if (node && !node.closest?.('[data-drag-handle], input[type="range"]')) press(node);
  }, {passive:true});
  document.addEventListener('keydown', event => {
    if (!['Enter',' '].includes(event.key) || event.repeat) return;
    const node = event.target?.closest?.('button, summary, [role="button"]');
    if (node) { press(node); intent(event); }
  });
  document.addEventListener('click', intent, true);
  document.addEventListener('change', intent, true);
  document.addEventListener('input', event => { if (event.target?.type === 'search') intent(event); }, true);
  document.addEventListener('toggle', event => {
    if (!enabled() || !event.target?.open) return;
    const node = event.target;
    if (node.tagName === 'DIALOG') { open(node); return; }
    const clicked = detailsGestures.get(node); detailsGestures.delete(node);
    if (clicked == null || Date.now() - clicked > 1600) return;
    Array.from(node.children || []).filter(child=>child.tagName !== 'SUMMARY').forEach((child,index) => {
      play(child,[{opacity:.15,transform:'translateY(-8px)'},{opacity:1,transform:'none'}],{duration:360,delay:Math.min(index*35,140)});
      draw(child,true);
    });
  }, true);
  document.addEventListener('cancel', event => {
    const dialog = event.target;
    if (dialog?.tagName !== 'DIALOG' || !dialog.open || !enabled()) return;
    event.preventDefault(); close(dialog, () => { if (dialog.open) dialog.close(); });
  }, true);
  window.addEventListener('hashchange', schedule);
  document.addEventListener('visibilitychange', () => {
    cancel(); lastKey = '';
    if (!document.hidden) {
      document.querySelectorAll('[data-motion]').forEach(node => {
        if (intersection && !seen.has(node) && observed.has(node)) { intersection.unobserve(node); intersection.observe(node); }
      });
      schedule();
    }
  });
  reduced?.addEventListener?.('change', () => { cancel(); lastKey = ''; schedule(); });
  const observer = typeof MutationObserver === 'function' ? new MutationObserver(schedule) : null;
  function watch() {
    roots.forEach(node => observer?.observe(node,{attributes:true,attributeFilter:['hidden','class']}));
    observer?.observe(document.body,{attributes:true,attributeFilter:['class','data-view']});
  }
  window.addEventListener('pagehide', () => { stopped=true;cancel();observer?.disconnect();intersection?.disconnect(); });
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    stopped=false;queued=false;lastKey='';observed=new WeakSet();forced=new WeakSet();watch();schedule();
  });
  watch(); schedule();
})();
