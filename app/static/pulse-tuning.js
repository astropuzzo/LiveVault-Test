/* LiveVault Pulse tuning — selectable time window, semantic patterns and adaptive time grid. */
(() => {
  'use strict';

  const RANGE_OPTIONS = [
    {hours: 4, label: '4h'},
    {hours: 6, label: '6h'},
    {hours: 8, label: '8h'},
    {hours: 12, label: '12h'},
    {hours: 24, label: '24h'},
    {hours: 72, label: '3 gg'},
    {hours: 168, label: '7 gg'},
  ];
  const ALLOWED_HOURS = RANGE_OPTIONS.map(option => option.hours);
  const storedHours = Number(localStorage.getItem('livevault-pulse-hours'));
  let selectedHours = ALLOWED_HOURS.includes(storedHours) ? storedHours : 6;
  let pulseRequest = null;
  let pulseRequestVersion = 0;
  const SVG_NS = 'http://www.w3.org/2000/svg';

  function svgNode(name, attrs = {}) {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }

  function patternRect(pattern, fill, width, height) {
    pattern.append(svgNode('rect', {x: 0, y: 0, width, height, fill}));
  }

  function ensurePatternDefs() {
    if (document.getElementById('lv-pulse-pattern-defs')) return;
    const host = svgNode('svg', {
      id: 'lv-pulse-pattern-defs',
      width: 0,
      height: 0,
      'aria-hidden': 'true',
      focusable: 'false',
    });
    const defs = svgNode('defs');

    const privatePattern = svgNode('pattern', {id: 'lv-pulse-private', width: 11, height: 11, patternUnits: 'userSpaceOnUse'});
    patternRect(privatePattern, '#a88bfa', 11, 11);
    privatePattern.append(svgNode('path', {d: 'M-3 11L11-3M3 14L14 3', stroke: '#1a1330', 'stroke-width': 1.4, opacity: .35}));
    defs.append(privatePattern);

    const tipjarPattern = svgNode('pattern', {id: 'lv-pulse-tipjar', width: 12, height: 12, patternUnits: 'userSpaceOnUse'});
    patternRect(tipjarPattern, '#f0943f', 12, 12);
    tipjarPattern.append(svgNode('circle', {cx: 3, cy: 3, r: 1.1, fill: '#2a1506', opacity: .4}));
    tipjarPattern.append(svgNode('circle', {cx: 9, cy: 9, r: 1.1, fill: '#2a1506', opacity: .4}));
    defs.append(tipjarPattern);

    const cloudPattern = svgNode('pattern', {id: 'lv-pulse-cloud', width: 18, height: 18, patternUnits: 'userSpaceOnUse'});
    patternRect(cloudPattern, '#3ecf8e', 18, 18);
    cloudPattern.append(svgNode('path', {
      d: 'M9 3.2L10 8L14.8 9L10 10L9 14.8L8 10L3.2 9L8 8Z',
      fill: '#06261a',
      opacity: .3,
    }));
    defs.append(cloudPattern);

    const processingPattern = svgNode('pattern', {id: 'lv-pulse-processing', width: 12, height: 12, patternUnits: 'userSpaceOnUse'});
    patternRect(processingPattern, '#6aa6ff', 12, 12);
    processingPattern.append(svgNode('path', {d: 'M-3 12L12-3M3 15L15 3', stroke: '#0b1a33', 'stroke-width': 1.4, opacity: .35}));
    defs.append(processingPattern);

    const missedPattern = svgNode('pattern', {id: 'lv-pulse-missed', width: 14, height: 14, patternUnits: 'userSpaceOnUse'});
    patternRect(missedPattern, '#ec6fb3', 14, 14);
    missedPattern.append(svgNode('path', {d: 'M0 8Q3.5 3.5 7 8T14 8', stroke: '#2d0b1d', 'stroke-width': 1.2, fill: 'none', opacity: .35}));
    defs.append(missedPattern);

    const restrictedPattern = svgNode('pattern', {id: 'lv-pulse-restricted', width: 10, height: 10, patternUnits: 'userSpaceOnUse'});
    patternRect(restrictedPattern, '#8b8b95', 10, 10);
    restrictedPattern.append(svgNode('path', {d: 'M2.5 0V10M7.5 0V10', stroke: '#16161a', 'stroke-width': 1.2, opacity: .35}));
    defs.append(restrictedPattern);

    const unrecordedPattern = svgNode('pattern', {id: 'lv-pulse-unrecorded', width: 12, height: 12, patternUnits: 'userSpaceOnUse'});
    patternRect(unrecordedPattern, '#56627a', 12, 12);
    unrecordedPattern.append(svgNode('path', {d: 'M-3 12L12-3M3 15L15 3', stroke: '#ec6fb3', 'stroke-width': 2, opacity: .85}));
    defs.append(unrecordedPattern);

    host.append(defs);
    document.body.prepend(host);
  }

  function pulseWindow() {
    const hours = Math.max(1, Number(controlRoomPulseData?.hours) || selectedHours);
    const generatedAt = timestamp(controlRoomPulseData?.generated_at) || Date.now();
    const expectedWindowStart = generatedAt - hours * 3600000;
    const apiWindowStart = timestamp(controlRoomPulseData?.window_start);
    const expectedSpan = hours * 3600000;
    const apiSpan = apiWindowStart ? generatedAt - apiWindowStart : 0;
    const windowStart = apiWindowStart && Math.abs(apiSpan - expectedSpan) <= 15 * 60000
      ? apiWindowStart
      : expectedWindowStart;
    return {hours, generatedAt, windowStart, span: Math.max(1, generatedAt - windowStart)};
  }

  function nextWholeHour(time) {
    const value = new Date(time);
    value.setMinutes(0, 0, 0);
    if (value.getTime() <= time) value.setHours(value.getHours() + 1);
    return value.getTime();
  }

  function wholeHourTicks(windowStart, generatedAt) {
    const ticks = [];
    for (let time = nextWholeHour(windowStart); time < generatedAt;) {
      ticks.push(time);
      const next = new Date(time);
      next.setHours(next.getHours() + 1);
      const nextTime = next.getTime();
      if (nextTime <= time) break;
      time = nextTime;
    }
    return ticks;
  }

  function halfHourTicks(windowStart, generatedAt) {
    const ticks = [];
    const value = new Date(windowStart);
    value.setSeconds(0, 0);
    if (value.getMinutes() < 30) value.setMinutes(30);
    else {
      value.setMinutes(0);
      value.setHours(value.getHours() + 1);
    }
    for (let time = value.getTime(); time < generatedAt; time += 30 * 60000) {
      if (new Date(time).getMinutes() === 30) ticks.push(time);
    }
    return ticks;
  }

  function localHour(time) {
    return Number(new Intl.DateTimeFormat('en-GB', {
      timeZone: DISPLAY_TIME_ZONE,
      hour: '2-digit',
      hourCycle: 'h23',
    }).format(new Date(time)));
  }

  function timeLabel(time, hours) {
    const longRange = hours > 24;
    return new Intl.DateTimeFormat('it-IT', longRange ? {
      timeZone: DISPLAY_TIME_ZONE,
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    } : {
      timeZone: DISPLAY_TIME_ZONE,
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).format(new Date(time));
  }

  function rangeDensity(hours) {
    if (hours <= 12) return {labelEvery: 1, strongEvery: 1, halfHours: true};
    if (hours <= 24) return {labelEvery: 2, strongEvery: 3, halfHours: false};
    if (hours <= 72) return {labelEvery: 6, strongEvery: 6, halfHours: false};
    return {labelEvery: 12, strongEvery: 6, halfHours: false};
  }

  function gridSvg(width, height, windowStart, generatedAt, span, hours) {
    const svg = svgNode('svg', {
      class: 'cr-pulse-hour-grid',
      viewBox: `0 0 ${width} ${height}`,
      preserveAspectRatio: 'none',
      'aria-hidden': 'true',
    });
    const density = rangeDensity(hours);
    const ticks = wholeHourTicks(windowStart, generatedAt);

    if (density.halfHours) {
      halfHourTicks(windowStart, generatedAt).forEach(time => {
        const x = (time - windowStart) / span * width;
        svg.append(svgNode('line', {class: 'cr-pulse-half-hour-line', x1: x.toFixed(2), x2: x.toFixed(2), y1: 0, y2: height}));
      });
    }

    ticks.forEach(time => {
      const x = (time - windowStart) / span * width;
      const hour = localHour(time);
      const dayBoundary = hour === 0;
      const strong = dayBoundary || hour % density.strongEvery === 0;
      const lineClass = dayBoundary ? 'cr-pulse-day-line' : strong ? 'cr-pulse-hour-line strong' : 'cr-pulse-hour-line minor';
      svg.append(svgNode('line', {class: lineClass, x1: x.toFixed(2), x2: x.toFixed(2), y1: 0, y2: height}));
    });
    return svg;
  }

  function axisSvg(width, windowStart, generatedAt, span, hours) {
    const svg = svgNode('svg', {
      class: 'cr-pulse-hour-axis',
      viewBox: `0 0 ${width} 26`,
      preserveAspectRatio: 'none',
      role: 'img',
      'aria-label': 'Scala oraria della cronologia',
    });
    const density = rangeDensity(hours);
    const ticks = wholeHourTicks(windowStart, generatedAt);
    let lastLabelX = -Infinity;
    const labelWidth = hours > 24 ? 100 : 42;
    ticks.forEach(time => {
      const x = (time - windowStart) / span * width;
      const hour = localHour(time);
      const dayBoundary = hour === 0;
      const showLabel = dayBoundary || hour % density.labelEvery === 0;
      svg.append(svgNode('line', {
        class: dayBoundary ? 'cr-pulse-hour-axis-tick day' : 'cr-pulse-hour-axis-tick',
        x1: x.toFixed(2), x2: x.toFixed(2), y1: 0, y2: dayBoundary ? 9 : 6,
      }));
      if (!showLabel || x < labelWidth / 2 || x > width - labelWidth / 2 || x - lastLabelX < labelWidth) return;
      lastLabelX = x;
      const text = svgNode('text', {
        class: dayBoundary ? 'cr-pulse-hour-axis-label day' : 'cr-pulse-hour-axis-label',
        x: x.toFixed(2), y: 21, 'text-anchor': 'middle',
      });
      text.textContent = timeLabel(time, hours);
      svg.append(text);
    });
    return svg;
  }

  const LEGEND = [
    ['online', 'ONLINE', '#56627a'],
    ['rec', 'REC', '#ff5c5c'],
    ['remote', 'CLOUD', 'url(#lv-pulse-cloud)'],
    ['processing', 'IN ELABORAZIONE', 'url(#lv-pulse-processing)'],
    ['missed', 'NON REC', 'url(#lv-pulse-missed)'],
    ['unrecorded', 'INTERVALLO SENZA REC', 'url(#lv-pulse-unrecorded)'],
    ['private', 'PRIVATA', 'url(#lv-pulse-private)'],
    ['tipjar', 'TIP-JAR', 'url(#lv-pulse-tipjar)'],
    ['restricted', 'LIMITATA', 'url(#lv-pulse-restricted)'],
  ];

  function legendSwatch(tone, fill) {
    return `<svg class="cr-pulse-legend-swatch" viewBox="0 0 12 12" aria-hidden="true"><rect class="legend-${tone}" x=".5" y=".5" width="11" height="11" rx="3" fill="${fill}"></rect></svg>`;
  }

  function centeredLegend(legend) {
    legend.innerHTML = LEGEND.map(([tone, label, fill]) =>
      `<span class="cr-pulse-legend-item">${legendSwatch(tone, fill)}${label}</span>`
    ).join('');
  }

  function decoratePulse() {
    const pulse = document.querySelector('.cr-pulse');
    if (!pulse) return;
    ensurePatternDefs();
    const {hours, windowStart, generatedAt, span} = pulseWindow();
    const head = pulse.querySelector('.cr-pulse-head');
    const right = pulse.querySelector('.cr-pulse-head-right, .cr-pulse-controls');
    const legend = pulse.querySelector('.cr-pulse-legend');

    if (head && right && legend) {
      centeredLegend(legend);
      if (legend.parentElement !== head) {
        legend.remove();
        head.insertBefore(legend, right);
      }
      right.className = 'cr-pulse-controls';
      right.innerHTML = `<label class="cr-pulse-range"><span>Finestra</span><select data-pulse-hours aria-label="Finestra temporale cronologia">${RANGE_OPTIONS.map(option => `<option value="${option.hours}"${option.hours === selectedHours ? ' selected' : ''}>${option.label}</option>`).join('')}</select></label>`;
    }

    const scaleTrack = pulse.querySelector('.cr-pulse-scale > div');
    if (scaleTrack) {
      const width = Math.max(1, Math.round(scaleTrack.getBoundingClientRect().width));
      scaleTrack.replaceChildren(axisSvg(width, windowStart, generatedAt, span, hours));
    }

    pulse.querySelectorAll('.cr-pulse-track').forEach(track => {
      track.querySelector('.cr-pulse-hour-grid')?.remove();
      const width = Math.max(1, Math.round(track.getBoundingClientRect().width));
      track.prepend(gridSvg(width, 22, windowStart, generatedAt, span, hours));
    });
  }

  loadControlRoomPulse = function loadControlRoomPulseTuned(force = false) {
    const requestedHours = selectedHours;
    if (pulseRequest?.hours === requestedHours) return pulseRequest.promise;
    const loadedHours = Number(controlRoomPulseData?.hours) || 0;
    if (!force && loadedHours === requestedHours && Date.now() - lastControlRoomPulseLoad < 20000) return Promise.resolve(controlRoomPulseData);
    const version = ++pulseRequestVersion;
    controlRoomPulseLoading = true;
    const promise = (async () => {
      try {
        const data = await api(`/api/control-room/pulse?hours=${selectedHours}`);
        if (version !== pulseRequestVersion) return controlRoomPulseData;
        if (!data || !Array.isArray(data.sessions) || Number(data.hours) !== requestedHours || !timestamp(data.generated_at)) throw new Error('Risposta cronologia non valida');
        controlRoomPulseData = data;
        controlRoomPulseError = '';
        lastControlRoomPulseLoad = Date.now();
      } catch (error) {
        if (version === pulseRequestVersion) controlRoomPulseError = error.message;
      } finally {
        if (version === pulseRequestVersion) {
          controlRoomPulseLoading = false;
          pulseRequest = null;
        }
      }
      return controlRoomPulseData;
    })();
    pulseRequest = {hours: requestedHours, promise};
    return promise;
  };

  const renderSourcesBase = renderSources;
  /* Phones: the timeline becomes wide and scrolls sideways with a finger,
     names stay pinned on the left, "Ora" jumps back to the present. */
  const PHONE = typeof window.matchMedia === 'function' ? window.matchMedia('(max-width: 620px)') : {matches: false};
  let pulseScroll = {left: 0, atEnd: true};
  window.pulsePixelsPerHour = () => {
    if (!PHONE.matches) return 0;
    const hours = Number(controlRoomPulseData?.hours) || selectedHours;
    return hours <= 24 ? 170 : hours <= 72 ? 60 : 28;
  };

  function mountPulseScroller() {
    if (typeof document.querySelector !== 'function') return;
    const pulse = document.querySelector('.cr-pulse');
    if (!pulse) return;
    const perHour = window.pulsePixelsPerHour();
    let scroller = pulse.querySelector('.cr-pulse-scroll');
    if (!perHour) {
      if (scroller) scroller.replaceWith(...scroller.childNodes);
      pulse.classList.remove('scrollable');
      pulse.querySelector('.cr-pulse-now')?.remove();
      return;
    }
    if (!scroller) {
      const items = [...pulse.querySelectorAll(':scope > .cr-pulse-scale, :scope > .cr-pulse-row')];
      if (!pulse.querySelector(':scope > .cr-pulse-row')) return;
      scroller = document.createElement('div');
      scroller.className = 'cr-pulse-scroll';
      items[0].before(scroller);
      scroller.append(...items);
      const now = document.createElement('button');
      now.type = 'button';
      now.className = 'cr-pulse-now';
      now.hidden = true;
      now.textContent = 'Ora ›';
      now.addEventListener('click', () => scroller.scrollTo({left: scroller.scrollWidth, behavior: 'smooth'}));
      scroller.before(now);
      scroller.addEventListener('scroll', () => {
        const atEnd = scroller.scrollLeft + scroller.clientWidth >= scroller.scrollWidth - 24;
        pulseScroll = {left: scroller.scrollLeft, atEnd};
        now.hidden = atEnd;
        pulse.classList.toggle('scrolled', scroller.scrollLeft > 4);
      }, {passive: true});
    }
    pulse.classList.add('scrollable');
    const hours = Number(controlRoomPulseData?.hours) || selectedHours;
    scroller.style.setProperty('--pulse-track', `${Math.round(hours * perHour)}px`);
    scroller.scrollLeft = pulseScroll.atEnd ? scroller.scrollWidth : pulseScroll.left;
    const now = pulse.querySelector('.cr-pulse-now');
    if (now) now.hidden = pulseScroll.atEnd;
  }

  renderSources = function renderSourcesWithPulseScale(...args) {
    const result = renderSourcesBase.apply(this, args);
    mountPulseScroller();
    requestAnimationFrame(() => { decoratePulse(); mountPulseScroller(); });
    return result;
  };
  PHONE.addEventListener?.('change', () => renderSources());

  document.addEventListener('change', async event => {
    const select = event.target.closest('[data-pulse-hours]');
    if (!select) return;
    const next = Number(select.value);
    if (!ALLOWED_HOURS.includes(next) || next === selectedHours) return;
    selectedHours = next;
    localStorage.setItem('livevault-pulse-hours', String(selectedHours));
    controlRoomPulseExpanded = false;
    hidePulseMediaPreview();
    const pending = loadControlRoomPulse(true);
    renderSources();
    await pending;
    renderSources();
  });

  let resizeTimer = 0;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => requestAnimationFrame(decoratePulse), 120);
  });

  // Deferred after app.js: boot may already have rendered before this override.
  if (!app.classList.contains('hidden')) loadControlRoomPulse().then(() => renderSources());
})();
