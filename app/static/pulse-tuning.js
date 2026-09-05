/* LiveVault Pulse tuning — selectable time window, centered legend and real hourly grid. */
(() => {
  'use strict';

  const ALLOWED_HOURS = [4, 6, 8, 12];
  const storedHours = Number(localStorage.getItem('livevault-pulse-hours'));
  let selectedHours = ALLOWED_HOURS.includes(storedHours) ? storedHours : 6;
  const SVG_NS = 'http://www.w3.org/2000/svg';

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
    for (let time = nextWholeHour(windowStart); time < generatedAt; ) {
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
    const minutes = value.getMinutes();
    if (minutes < 30) value.setMinutes(30);
    else {
      value.setMinutes(0);
      value.setHours(value.getHours() + 1);
    }
    for (let time = value.getTime(); time < generatedAt; time += 30 * 60000) {
      if (new Date(time).getMinutes() === 30) ticks.push(time);
    }
    return ticks;
  }

  function timeLabel(time) {
    return new Intl.DateTimeFormat('it-IT', {
      timeZone: DISPLAY_TIME_ZONE,
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).format(new Date(time));
  }

  function svgNode(name, attrs = {}) {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }

  function gridSvg(width, height, windowStart, generatedAt, span) {
    const svg = svgNode('svg', {
      class: 'cr-pulse-hour-grid',
      viewBox: `0 0 ${width} ${height}`,
      preserveAspectRatio: 'none',
      'aria-hidden': 'true',
    });
    const hourTicks = wholeHourTicks(windowStart, generatedAt);
    const halfTicks = halfHourTicks(windowStart, generatedAt);

    halfTicks.forEach(time => {
      const x = (time - windowStart) / span * width;
      svg.append(svgNode('line', {class: 'cr-pulse-half-hour-line', x1: x.toFixed(2), x2: x.toFixed(2), y1: 0, y2: height}));
    });
    hourTicks.forEach((time, index) => {
      const x = (time - windowStart) / span * width;
      if (index % 2 === 0) {
        const prev = index === 0 ? windowStart : hourTicks[index - 1];
        const bandStart = Math.max(windowStart, prev);
        const bx = (bandStart - windowStart) / span * width;
        svg.append(svgNode('rect', {class: 'cr-pulse-hour-band', x: bx.toFixed(2), y: 0, width: Math.max(0, x - bx).toFixed(2), height}));
      }
      svg.append(svgNode('line', {class: 'cr-pulse-hour-line', x1: x.toFixed(2), x2: x.toFixed(2), y1: 0, y2: height}));
    });
    return svg;
  }

  function axisSvg(width, windowStart, generatedAt, span) {
    const svg = svgNode('svg', {
      class: 'cr-pulse-hour-axis',
      viewBox: `0 0 ${width} 24`,
      preserveAspectRatio: 'none',
      role: 'img',
      'aria-label': 'Scala oraria della cronologia',
    });
    wholeHourTicks(windowStart, generatedAt).forEach(time => {
      const x = (time - windowStart) / span * width;
      svg.append(svgNode('line', {class: 'cr-pulse-hour-axis-tick', x1: x.toFixed(2), x2: x.toFixed(2), y1: 0, y2: 7}));
      const text = svgNode('text', {class: 'cr-pulse-hour-axis-label', x: x.toFixed(2), y: 19, 'text-anchor': 'middle'});
      text.textContent = timeLabel(time);
      svg.append(text);
    });
    return svg;
  }

  function centeredLegend(legend) {
    legend.innerHTML = [
      ['live', 'ONLINE'],
      ['private', 'PRIVATA'],
      ['tipjar', 'TIP-JAR'],
      ['rec', 'REC'],
      ['remote', 'CLOUD'],
      ['processing', 'RECUPERO'],
      ['missed', 'NON REC'],
    ].map(([tone, label]) => `<span class="cr-pulse-legend-item"><i class="${tone}"></i>${label}</span>`).join('');
  }

  function decoratePulse() {
    const pulse = document.querySelector('.cr-pulse');
    if (!pulse) return;
    const {windowStart, generatedAt, span} = pulseWindow();
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
      right.innerHTML = `<label class="cr-pulse-range"><span>Finestra</span><select data-pulse-hours aria-label="Finestra temporale cronologia">${ALLOWED_HOURS.map(hours => `<option value="${hours}"${hours === selectedHours ? ' selected' : ''}>${hours}h</option>`).join('')}</select></label>`;
    }

    const scaleTrack = pulse.querySelector('.cr-pulse-scale > div');
    if (scaleTrack) {
      const width = Math.max(1, Math.round(scaleTrack.getBoundingClientRect().width));
      scaleTrack.replaceChildren(axisSvg(width, windowStart, generatedAt, span));
    }

    pulse.querySelectorAll('.cr-pulse-track').forEach(track => {
      track.querySelector('.cr-pulse-hour-grid')?.remove();
      const width = Math.max(1, Math.round(track.getBoundingClientRect().width));
      track.prepend(gridSvg(width, 22, windowStart, generatedAt, span));
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
