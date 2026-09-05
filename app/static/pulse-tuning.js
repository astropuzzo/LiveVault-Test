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
  const SVG_NS = 'http://www.w3.org/2000/svg';

  function svgNode(name, attrs = {}) {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }

  function patternRect(pattern, fill) {
    pattern.append(svgNode('rect', {x: 0, y: 0, width: '100%', height: '100%', fill}));
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

    const privatePattern = svgNode('pattern', {id: 'lv-pulse-private', width: 8, height: 8, patternUnits: 'userSpaceOnUse'});
    patternRect(privatePattern, '#9a5cff');
    privatePattern.append(svgNode('path', {d: 'M-2 8L8-2M2 10L10 2', stroke: '#e4d4ff', 'stroke-width': 1.35, opacity: .78}));
    defs.append(privatePattern);

    const tipjarPattern = svgNode('pattern', {id: 'lv-pulse-tipjar', width: 9, height: 9, patternUnits: 'userSpaceOnUse'});
    patternRect(tipjarPattern, '#f1a72a');
    tipjarPattern.append(svgNode('circle', {cx: 2.2, cy: 2.2, r: 1.15, fill: '#fff0b7', opacity: .92}));
    tipjarPattern.append(svgNode('circle', {cx: 6.7, cy: 6.7, r: 1.15, fill: '#8b5908', opacity: .72}));
    defs.append(tipjarPattern);

    const cloudPattern = svgNode('pattern', {id: 'lv-pulse-cloud', width: 12, height: 12, patternUnits: 'userSpaceOnUse'});
    patternRect(cloudPattern, '#32d583');
    cloudPattern.append(svgNode('path', {
      d: 'M6 1.2L7.1 4.9L10.8 6L7.1 7.1L6 10.8L4.9 7.1L1.2 6L4.9 4.9Z',
      fill: '#e9fff3',
      opacity: .74,
    }));
    defs.append(cloudPattern);

    const processingPattern = svgNode('pattern', {id: 'lv-pulse-processing', width: 8, height: 8, patternUnits: 'userSpaceOnUse'});
    patternRect(processingPattern, '#22c7ff');
    processingPattern.append(svgNode('path', {d: 'M0 0L8 8M8 0L0 8', stroke: '#d8f7ff', 'stroke-width': .9, opacity: .66}));
    defs.append(processingPattern);

    const missedPattern = svgNode('pattern', {id: 'lv-pulse-missed', width: 10, height: 10, patternUnits: 'userSpaceOnUse'});
    patternRect(missedPattern, '#ff4fc8');
    missedPattern.append(svgNode('path', {d: 'M0 5L2.5 2.5L5 5L7.5 2.5L10 5M0 10L2.5 7.5L5 10L7.5 7.5L10 10', stroke: '#ffd2f1', 'stroke-width': 1, fill: 'none', opacity: .72}));
    defs.append(missedPattern);

    const restrictedPattern = svgNode('pattern', {id: 'lv-pulse-restricted', width: 7, height: 7, patternUnits: 'userSpaceOnUse'});
    patternRect(restrictedPattern, '#77818c');
    restrictedPattern.append(svgNode('path', {d: 'M1 0V7M4.5 0V7', stroke: '#dce2e8', 'stroke-width': .8, opacity: .55}));
    defs.append(restrictedPattern);

    const unrecordedPattern = svgNode('pattern', {id: 'lv-pulse-unrecorded', width: 9, height: 9, patternUnits: 'userSpaceOnUse'});
    patternRect(unrecordedPattern, '#ff7a3d');
    unrecordedPattern.append(svgNode('path', {d: 'M-2 7L2.5 2.5L7 7L11.5 2.5', stroke: '#ffe1d0', 'stroke-width': 1, fill: 'none', opacity: .72}));
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
    ticks.forEach(time => {
      const x = (time - windowStart) / span * width;
      const hour = localHour(time);
      const dayBoundary = hour === 0;
      const showLabel = dayBoundary || hour % density.labelEvery === 0;
      svg.append(svgNode('line', {
        class: dayBoundary ? 'cr-pulse-hour-axis-tick day' : 'cr-pulse-hour-axis-tick',
        x1: x.toFixed(2), x2: x.toFixed(2), y1: 0, y2: dayBoundary ? 9 : 6,
      }));
      if (!showLabel) return;
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
    ['online', 'ONLINE', '#2f7cff'],
    ['private', 'PRIVATA', 'url(#lv-pulse-private)'],
    ['tipjar', 'TIP-JAR', 'url(#lv-pulse-tipjar)'],
    ['rec', 'REC', '#ff4f62'],
    ['remote', 'CLOUD', 'url(#lv-pulse-cloud)'],
    ['processing', 'RECUPERO', 'url(#lv-pulse-processing)'],
    ['missed', 'NON REC', 'url(#lv-pulse-missed)'],
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

  loadControlRoomPulse = async function loadControlRoomPulseTuned(force = false) {
    const loadedHours = Number(controlRoomPulseData?.hours) || 0;
    if (!force && loadedHours === selectedHours && Date.now() - lastControlRoomPulseLoad < 20000) return controlRoomPulseData;
    try {
      controlRoomPulseData = await api(`/api/control-room/pulse?hours=${selectedHours}`);
      lastControlRoomPulseLoad = Date.now();
    } catch (error) {
      if (error.message !== 'auth') console.warn('Live Pulse:', error.message);
    }
    return controlRoomPulseData;
  };

  const renderSourcesBase = renderSources;
  renderSources = function renderSourcesWithPulseScale(...args) {
    const result = renderSourcesBase.apply(this, args);
    requestAnimationFrame(decoratePulse);
    return result;
  };

  document.addEventListener('change', async event => {
    const select = event.target.closest('[data-pulse-hours]');
    if (!select) return;
    const next = Number(select.value);
    if (!ALLOWED_HOURS.includes(next) || next === selectedHours) return;
    selectedHours = next;
    localStorage.setItem('livevault-pulse-hours', String(selectedHours));
    lastControlRoomPulseLoad = 0;
    select.disabled = true;
    await loadControlRoomPulse(true);
    renderSources();
  });

  let resizeTimer = 0;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => requestAnimationFrame(decoratePulse), 120);
  });

  lastControlRoomPulseLoad = 0;
  loadControlRoomPulse(true).then(() => renderSources());
})();
