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

    // Pattern is secondary to hue: each tile is fully painted in the semantic
    // color first, then receives a restrained low-contrast texture.
    const privatePattern = svgNode('pattern', {id: 'lv-pulse-private', width: 12, height: 12, patternUnits: 'userSpaceOnUse'});
    patternRect(privatePattern, '#9a5cff', 12, 12);
    privatePattern.append(svgNode('path', {
      d: 'M-2 12L12-2M5 14L14 5',
      stroke: '#c9b7ff',
      'stroke-width': .8,
      opacity: .18,
    }));
    defs.append(privatePattern);

    const tipjarPattern = svgNode('pattern', {id: 'lv-pulse-tipjar', width: 12, height: 12, patternUnits: 'userSpaceOnUse'});
    patternRect(tipjarPattern, '#f1a72a', 12, 12);
    tipjarPattern.append(svgNode('circle', {cx: 3.5, cy: 3.5, r: .85, fill: '#6f480a', opacity: .32}));
    tipjarPattern.append(svgNode('circle', {cx: 9.5, cy: 9.5, r: .65, fill: '#ffe1a0', opacity: .20}));
    defs.append(tipjarPattern);

    const cloudPattern = svgNode('pattern', {id: 'lv-pulse-cloud', width: 22, height: 22, patternUnits: 'userSpaceOnUse'});
    patternRect(cloudPattern, '#32d583', 22, 22);
    cloudPattern.append(svgNode('path', {
      d: 'M11 6L11.8 8.2L14 9L11.8 9.8L11 12L10.2 9.8L8 9L10.2 8.2Z',
      fill: '#c7f5dc',
      opacity: .18,
    }));
    defs.append(cloudPattern);

    const processingPattern = svgNode('pattern', {id: 'lv-pulse-processing', width: 14, height: 14, patternUnits: 'userSpaceOnUse'});
    patternRect(processingPattern, '#22c7ff', 14, 14);
    processingPattern.append(svgNode('path', {
      d: 'M4 4L10 10M10 4L4 10',
      stroke: '#b7edf8',
      'stroke-width': .7,
      opacity: .17,
    }));
    defs.append(processingPattern);

    const missedPattern = svgNode('pattern', {id: 'lv-pulse-missed', width: 20, height: 12, patternUnits: 'userSpaceOnUse'});
    patternRect(missedPattern, '#ff4fc8', 20, 12);
    missedPattern.append(svgNode('path', {
      d: 'M0 7Q5 2 10 7T20 7',
      stroke: '#f0a7d4',
      'stroke-width': .75,
      fill: 'none',
      opacity: .20,
    }));
    defs.append(missedPattern);

    const restrictedPattern = svgNode('pattern', {id: 'lv-pulse-restricted', width: 12, height: 12, patternUnits: 'userSpaceOnUse'});
    patternRect(restrictedPattern, '#77818c', 12, 12);
    restrictedPattern.append(svgNode('path', {
      d: 'M3 0V12M9 0V12',
      stroke: '#cbd2d8',
      'stroke-width': .7,
      opacity: .18,
    }));
    defs.append(restrictedPattern);

    const unrecordedPattern = svgNode('pattern', {id: 'lv-pulse-unrecorded', width: 14, height: 12, patternUnits: 'userSpaceOnUse'});
    patternRect(unrecordedPattern, '#ff7a3d', 14, 12);
    unrecordedPattern.append(svgNode('path', {
      d: 'M-2 9L3 4L8 9L13 4L18 9',
      stroke: '#ffd1ba',
      'stroke-width': .75,
      fill: 'none',
      opacity: .20,
    }));
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
