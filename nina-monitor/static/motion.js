/* OpenAstro motion: finite entrances; shared by all three workspaces.
   Visibility and reduced motion cancel work. Polling never replays an entrance. */
(() => {
  'use strict';
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)');
  const seen = new WeakSet(), active = new Set();
  let observed = new WeakSet();
  let queued = false, stopped = false, lastKey = ''; 
  const roots = [...['app', 'appView', 'login', 'loginView', 'loginGate', 'dashboard']
    .map(id => document.getElementById(id)).filter(Boolean),
    ...document.querySelectorAll('[data-page], [data-view-panel]')];
  const visible = node => !node.hidden && node.getClientRects().length > 0;
  function cancel() { active.forEach(animation => animation.cancel()); active.clear(); }
  function enter(node, index = 0) {
    if (stopped || seen.has(node) || !visible(node) || document.hidden || reduced?.matches) return;
    seen.add(node);
    const animation = node.animate([
      {opacity: .25, transform: 'translateY(16px) scale(.99)'},
      {opacity: 1, transform: 'translateY(0) scale(1)'},
    ], {duration: 560, delay: Math.min(index * 55, 220), easing: 'cubic-bezier(.22,1,.36,1)', fill: 'backwards'});
    active.add(animation);
    animation.finished.then(() => active.delete(animation), () => active.delete(animation));
  }
  const intersection = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
    entries.filter(entry => entry.isIntersecting).forEach((entry, index) => {
      // Keep a hidden-page entry observable until the tab is visible again.
      if (stopped || document.hidden || reduced?.matches) return;
      enter(entry.target, index);
      intersection.unobserve(entry.target);
    });
  }, {threshold: .06}) : null;
  function reveal() {
    queued = false;
    if (stopped || document.hidden || reduced?.matches) { cancel(); return; }
    const key = `${location.pathname}|${location.hash}|${document.body.dataset.view || ''}|${roots.map(node => visible(node) ? '1' : '0').join('')}`;
    if (key === lastKey) return;
    lastKey = key;
    if (typeof Element.prototype.animate !== 'function') return;
    Array.from(document.querySelectorAll('[data-motion]')).filter(node => visible(node) && !seen.has(node)).forEach((node, index) => {
      if (!intersection) return enter(node, index);
      if (!observed.has(node)) { observed.add(node); intersection.observe(node); }
    });
  }
  function schedule() {
    if (stopped || queued || document.hidden) return;
    queued = true;
    window.requestAnimationFrame(reveal);
  }
  window.addEventListener('hashchange', schedule);
  document.addEventListener('visibilitychange', () => {
    cancel(); lastKey = '';
    if (!document.hidden) {
      // Restart intersection observation; a hidden tab may consume a threshold crossing.
      document.querySelectorAll('[data-motion]').forEach(node => {
        if (intersection && !seen.has(node) && observed.has(node)) { intersection.unobserve(node); intersection.observe(node); }
      });
      schedule();
    }
  });
  reduced?.addEventListener?.('change', () => { cancel(); lastKey = ''; schedule(); });
  const observer = typeof MutationObserver === 'function' ? new MutationObserver(schedule) : null;
  function watch() {
    roots.forEach(node => observer?.observe(node, {attributes: true, attributeFilter: ['hidden', 'class']}));
    observer?.observe(document.body, {attributes: true, attributeFilter: ['class', 'data-view']});
  }
  window.addEventListener('pagehide', () => { stopped = true; cancel(); observer?.disconnect(); intersection?.disconnect(); });
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    stopped = false; queued = false; lastKey = ''; observed = new WeakSet(); watch(); schedule();
  });
  watch();
  schedule();
})();
