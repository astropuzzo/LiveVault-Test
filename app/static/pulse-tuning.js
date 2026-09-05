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
    patternRect(privatePattern, '#9a5cff', 11, 11);
    privatePattern.append(svgNode('path', {d: 'M-3 11L11-3M3 14L14 3', stroke: '#e2d4ff', 'stroke-width': 1.05, opacity: .42}));
    defs.append(privatePattern);

    const tipjarPattern = svgNode('pattern', {id: 'lv-pulse-tipjar', width: 12, height: 12, patternUnits: 'userSpaceOnUse'});
    patternRect(tipjarPattern, '#f1a72a', 12, 12);
    tipjarPattern.append(svgNode('circle', {cx: 3, cy: 3, r: .95, fill: '#fff0b7', opacity: .46}));
    tipjarPattern.append(svgNode('circle', {cx: 9, cy: 9, r: .95, fill: '#85540a', opacity: .52}));
    defs.append(tipjarPattern);

    const cloudPattern = svgNode('pattern', {id: 'lv-pulse-cloud', width: 18, height: 18, patternUnits: 'userSpaceOnUse'});
    patternRect(cloudPattern, '#32d583', 18, 18);
    cloudPattern.append(svgNode('path', {
      d: 'M9 3.2L10 8L14.8 9L10 10L9 14.8L8 10L3.2 9L8 8Z',
      fill: '#e9fff3',
      opacity: .36,
    }));
    defs.append(cloudPattern);

    const processingPattern = svgNode('pattern', {id: 'lv-pulse-processing', width: 12, height: 12, patternUnits: 'userSpaceOnUse'});
    patternRect(processingPattern, '#22c7ff', 12, 12);
    processingPattern.append(svgNode('path', {d: 'M3 3L9 9M9 3L3 9', stroke: '#d8f7ff', 'stroke-width': .85, opacity: .38}));
    defs.append(processingPattern);

    const missedPattern = svgNode('pattern', {id: 'lv-pulse-missed', width: 14, height: 14, patternUnits: 'userSpaceOnUse'});
    patternRect(missedPattern, '#ff4fc8', 14, 14);
    missedPattern.append(svgNode('path', {d: 'M0 8Q3.5 3.5 7 8T14 8', stroke: '#ffd2f1', 'stroke-width': 1, fill: 'none', opacity: .42}));
    defs.append(missedPattern);

    const restrictedPattern = svgNode('pattern', {id: 'lv-pulse-restricted', width: 10, height: 10, patternUnits: 'userSpaceOnUse'});
    patternRect(restrictedPattern, '#77818c', 10, 10);
    restrictedPattern.append(svgNode('path', {d: 'M2.5 0V10M7.5 0V10', stroke: '#dce2e8', 'stroke-width': .8, opacity: .38}));
    defs.append(restrictedPattern);

    const unrecordedPattern = svgNode('pattern', {id: 'lv-pulse-unrecorded', width: 12, height: 12, patternUnits: 'userSpaceOnUse'});
    patternRect(unrecordedPattern, '#ff7a3d', 12, 12);
    unrecordedPattern.append(svgNode('path', {d: 'M-3 8L2 3L7 8L12 3L17 8', stroke: '#ffe1d0', 'stroke-width': 1, fill: 'none', opacity: .4}));
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
