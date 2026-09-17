(() => {
  'use strict';
  const MF = window.MultiFilter;
  const $ = id => document.getElementById(id);
  if (!$('view-analytics')) return;
  let data = null, requestId = 0, chartDate = '';
  const human = day => day.split('-').reverse().join('.');
  const number = value => value.toLocaleString('ru-RU');
  const delta = value => (value > 0 ? '+' : '') + number(value);
  const node = (tag, text, className) => {
    const item = document.createElement(tag);
    if (text != null) item.textContent = text;
    if (className) item.className = className;
    return item;
  };
  const svgNode = (tag, attrs, text) => {
    const item = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) item.setAttribute(key, value);
    if (text != null) item.textContent = text;
    return item;
  };
  function chart(counts, dates, compact = false) {
    const width = compact ? 160 : Math.max(240, Math.min(760, window.innerWidth - 64)), height = compact ? 40 : 250;
    const left = compact ? 3 : 48, right = width - (compact ? 3 : 25);
    const top = compact ? 4 : 22, bottom = height - (compact ? 4 : 38);
    const max = Math.max(1, ...counts);
    const points = counts.map((value, i) => [counts.length === 1 ? (left + right) / 2 : left + i * (right - left) / (counts.length - 1), bottom - value / max * (bottom - top)]);
    const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', class: compact ? 'analytics-sparkline' : 'analytics-chart'});
    svg.append(svgNode('title', {}, dates.map((day, i) => human(day) + ': ' + counts[i] + ' чел.').join('; ')));
    if (!compact) {
      [...new Set([0, Math.round(max / 2), max])].forEach(value => {
        const y = bottom - value / max * (bottom - top);
        svg.append(svgNode('line', {x1: left, y1: y, x2: right, y2: y, class: 'analytics-gridline'}),
          svgNode('text', {x: left - 8, y: y + 4, 'text-anchor': 'end'}, number(value)));
      });
      [...new Set([0, Math.floor((dates.length - 1) / 2), dates.length - 1])].forEach(i =>
        svg.append(svgNode('text', {x: points[i][0], y: height - 12,
          'text-anchor': i === 0 ? 'start' : i === dates.length - 1 ? 'end' : 'middle'}, human(dates[i]).slice(0, 5))));
      if (points.length > 1) svg.append(svgNode('polygon', {points: [[points[0][0], bottom], ...points, [points.at(-1)[0], bottom]].map(p => p.join(',')).join(' '), class: 'analytics-area'}));
    }
    svg.append(svgNode('polyline', {points: points.map(p => p.join(',')).join(' '), class: 'analytics-line', 'vector-effect': 'non-scaling-stroke'}));
    points.forEach(([x, y], i) => {
      const dot = svgNode('circle', {cx: x, cy: y, r: compact ? 1.5 : 3, class: 'analytics-dot'});
      dot.append(svgNode('title', {}, human(dates[i]) + ': ' + counts[i] + ' чел.')); svg.append(dot);
    });
    return svg;
  }
  const categoryColor = value => /^#[0-9a-f]{6}$/i.test(value || '') ? value : '#94A3B8';
  function categoryChart(series, counts, dates) {
    const valid = Array.isArray(series) && series.every(item => Array.isArray(item.counts) && item.counts.length === dates.length &&
      item.counts.every(value => Number.isFinite(value) && value >= 0));
    if (!valid || dates.some((day, index) => series.reduce((sum, item) => sum + item.counts[index], 0) !== counts[index])) {
      return node('p', 'Распределение по категориям не соответствует общему итогу. Обновите данные.', 'error-text');
    }
    const shown = series.filter(item => item.counts.some(value => value > 0));
    const width = Math.max(220, ($('view-analytics').clientWidth || window.innerWidth - 72) - (window.matchMedia('(max-width: 900px)').matches ? 24 : 270));
    const height = 210, left = 42, right = width - 12, top = 12, bottom = height - 28;
    const max = Math.max(1, ...counts), x = index => dates.length === 1 ? (left + right) / 2 : left + index * (right - left) / (dates.length - 1);
    const y = value => bottom - value / max * (bottom - top);
    const layout = node('div', null, 'analytics-stack-layout'), main = node('div', null, 'analytics-stack-main');
    const plot = node('div', null, 'analytics-plot');
    plot.tabIndex = 0;plot.setAttribute('role', 'slider');plot.setAttribute('aria-label', 'Дата диаграммы расстановки');
    plot.setAttribute('aria-valuemin', '0');plot.setAttribute('aria-valuemax', String(Math.max(0, dates.length - 1)));
    plot.setAttribute('aria-describedby', 'analytics-chart-note');
    const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, class: 'analytics-chart analytics-stacked-chart', 'aria-hidden': 'true'});
    const levels = [...new Set([0, Math.round(max / 2), max])];
    levels.forEach(value => svg.append(svgNode('line', {x1: left, y1: y(value), x2: right, y2: y(value), class: 'analytics-gridline'}),
      svgNode('text', {x: left - 7, y: y(value) + 4, 'text-anchor': 'end'}, number(value))));
    let previous = dates.map(() => 0);
    shown.forEach(item => {
      const upper = previous.map((value, index) => value + item.counts[index]);
      const points = dates.length === 1 ? [[left, y(upper[0])], [right, y(upper[0])], [right, y(previous[0])], [left, y(previous[0])]] :
        [...upper.map((value, index) => [x(index), y(value)]), ...previous.map((value, index) => [x(index), y(value)]).reverse()];
      const area = svgNode('polygon', {points: points.map(point => point.join(',')).join(' '), fill: categoryColor(item.color),
        stroke: categoryColor(item.color), 'stroke-width': '.5', 'vector-effect': 'non-scaling-stroke', class: 'analytics-category-area'});
      svg.append(area);previous = upper;
    });
    [...new Set([0, Math.floor((dates.length - 1) / 2), dates.length - 1])].forEach(index =>
      svg.append(svgNode('text', {x: x(index), y: height - 7,
        'text-anchor': dates.length === 1 ? 'middle' : index === 0 ? 'start' : index === dates.length - 1 ? 'end' : 'middle'}, human(dates[index]).slice(0, 5))));
    const cursor = svgNode('line', {x1: left, y1: top, x2: left, y2: bottom, class: 'analytics-date-cursor'});
    const dot = svgNode('circle', {cx: left, cy: bottom, r: '3', class: 'analytics-total-dot'});
    svg.append(cursor, dot);plot.append(svg);
    const legend = node('aside', null, 'analytics-category-legend');legend.setAttribute('aria-label', 'Категории ГДЛР и численность на выбранную дату');
    const caption = node('div', null, 'analytics-legend-heading'), dayLabel = node('strong'), total = node('strong');
    caption.append(dayLabel, total);legend.append(caption);
    const list = node('ul'), values = [];
    shown.forEach(item => {
      const row = node('li'), swatch = node('span', null, 'analytics-category-swatch'), value = node('strong', '0');
      swatch.style.backgroundColor = categoryColor(item.color);swatch.setAttribute('aria-hidden', 'true');
      row.append(swatch, node('span', item.name), value);list.append(row);values.push(value);
    });
    if (!shown.length) list.append(node('li', 'Назначений за период нет.', 'analytics-legend-empty'));
    legend.append(list);
    const controls = node('div', null, 'analytics-date-controls');
    const back = node('button', '‹', 'secondary-button'), forward = node('button', '›', 'secondary-button');
    back.type = forward.type = 'button';back.setAttribute('aria-label', 'Предыдущая дата диаграммы');forward.setAttribute('aria-label', 'Следующая дата диаграммы');
    const input = node('input');input.type = 'date';input.min = dates[0];input.max = dates.at(-1);input.setAttribute('aria-label', 'Точные значения на дату');
    const readout = node('span', null, 'analytics-chart-readout');readout.setAttribute('role', 'status');readout.setAttribute('aria-live', 'polite');
    controls.append(back, input, forward, readout);
    let index = dates.includes(chartDate) ? dates.indexOf(chartDate) : dates.length - 1;
    function select(next) {
      index = Math.max(0, Math.min(dates.length - 1, next));chartDate = dates[index];input.value = chartDate;
      dayLabel.textContent = human(chartDate);total.textContent = number(counts[index]) + ' чел.';
      values.forEach((value, i) => {value.textContent = number(shown[i].counts[index]);});
      cursor.setAttribute('x1', x(index));cursor.setAttribute('x2', x(index));dot.setAttribute('cx', x(index));dot.setAttribute('cy', y(counts[index]));
      plot.setAttribute('aria-valuenow', String(index));plot.setAttribute('aria-valuetext', human(chartDate) + ': ' + number(counts[index]) + ' человек');
      readout.textContent = 'Всего: ' + number(counts[index]);back.disabled = index === 0;forward.disabled = index === dates.length - 1;
    }
    function point(event) {
      const rect = svg.getBoundingClientRect();if (!rect.width) return;
      const offset = (event.clientX - rect.left) * width / rect.width;
      select(dates.length === 1 ? 0 : Math.round((offset - left) / (right - left) * (dates.length - 1)));
    }
    plot.addEventListener('pointermove', event => {if (event.pointerType !== 'touch' || event.buttons) point(event);});
    plot.addEventListener('pointerdown', event => {point(event);plot.focus({preventScroll: true});});
    plot.addEventListener('keydown', event => {
      const next = {ArrowLeft: index - 1, ArrowDown: index - 1, ArrowRight: index + 1, ArrowUp: index + 1, Home: 0, End: dates.length - 1}[event.key];
      if (next !== undefined) {event.preventDefault();select(next);}
    });
    back.addEventListener('click', () => select(index - 1));forward.addEventListener('click', () => select(index + 1));
    input.addEventListener('change', () => {const next = dates.indexOf(input.value);select(next < 0 ? index : next);});
    select(index);main.append(plot, controls);layout.append(main, legend);return layout;
  }
  MF.enable($('analytics-user'));
  function render() {
    if (!data) return;
    const chosen = MF.get($('analytics-user'));
    const user = data.users.find(item => item.id === chosen);
    const counts = data.counts;
    const unique = data.unique_count;
    const last = counts.at(-1), change = last - counts[0];
    const metrics = [['На конец периода', number(last)], ['Изменение за период', delta(change)],
      ['Уникальных за период', number(unique)], ['Авторов с назначениями', number(data.users.filter(item => item.unique_count && item.id !== 'unknown' && MF.matches(chosen,item.id)).length)]];
    $('analytics-stats').replaceChildren(...metrics.map(([label, value]) => {
      const item = node('div', null, 'analytics-metric'); item.append(node('span', label), node('strong', value)); return item;
    }));
    $('analytics-chart-title').textContent = user ? user.full_name : chosen ? 'Персонал выбранных пользователей' : 'Весь расставленный персонал';
    $('analytics-period').textContent = human(data.dates[0]) + ' — ' + human(data.dates.at(-1));
    $('analytics-chart').replaceChildren(categoryChart(data.category_series, counts, data.dates));
    $('analytics-chart-note').textContent = 'Текущие категории ГДЛР. Наведите, коснитесь графика или используйте стрелки клавиатуры — значения появятся в легенде. Цвета задаются в справочнике.';
    const selectedDate = $('analytics-detail-date').value;
    const dayIndex = data.dates.indexOf(selectedDate);
    const headers = ['Пользователь', human(selectedDate), 'Изменение', 'Динамика', 'Уникальных за период'];
    const table = node('table', null, 'analytics-users-table');
    const head = node('thead'), heading = node('tr');
    headers.forEach(label => { const th = node('th', label); th.scope = 'col'; heading.append(th); });
    head.append(heading); table.append(head);
    const body = node('tbody');
    const users = data.users.filter(item => MF.matches(chosen,item.id)).slice().sort((a, b) => b.counts[dayIndex] - a.counts[dayIndex] || b.unique_count - a.unique_count || a.full_name.localeCompare(b.full_name, 'ru'));
    $('analytics-users-count').textContent = number(users.length);
    const names = new Map();
    for (const item of data.users) names.set(item.full_name, (names.get(item.full_name) || 0) + 1);
    users.forEach(item => {
      const row = node('tr');
      const nameCell = node('td'); nameCell.dataset.label = headers[0];
      const button = node('button', item.full_name + (names.get(item.full_name) > 1 ? ' · №' + item.id : ''), 'text-button analytics-user-button');
      button.type = 'button'; button.addEventListener('click', () => { MF.set($('analytics-user'), item.id); load(); });
      nameCell.append(button); row.append(nameCell);
      [number(item.counts[dayIndex]), delta(item.counts[dayIndex] - item.counts[0]), chart(item.counts, data.dates, true), number(item.unique_count)].forEach((value, i) => {
        const cell = node('td'); cell.dataset.label = headers[i + 1]; cell.append(value); row.append(cell);
      });
      body.append(row);
    });
    table.append(body);
    $('analytics-users').replaceChildren(users.length ? table : node('p', 'Нет пользователей с назначениями в доступных бригадах.'));
  }
  async function load() {
    window.userActivityDashboard?.load();
    const current = ++requestId;
    $('analytics-status').textContent = 'Загрузка динамики…'; $('analytics-status').classList.remove('error-text');
    $('analytics-content').hidden = true;
    const start = $('analytics-start').value, end = $('analytics-end').value;
    try {
      const response = await fetch('/api/personnel-dashboard?' + MF.params(new URLSearchParams({start, end}), 'user', MF.get($('analytics-user'))), {cache: 'no-store'});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Не удалось загрузить дашборд.');
      if (current !== requestId) return;
      data = result;
      const chosen = MF.get($('analytics-user'));
      const all = node('option', 'Все пользователи'); all.value = '';
      const names = new Map(); data.users.forEach(item => names.set(item.full_name, (names.get(item.full_name) || 0) + 1));
      $('analytics-user').replaceChildren(all, ...data.users.map(item => {
        const option = node('option', item.full_name + (names.get(item.full_name) > 1 ? ' · №' + item.id : '')); option.value = item.id; return option;
      }));
      MF.set($('analytics-user'),chosen);
      const detail = $('analytics-detail-date'); detail.min = data.dates[0]; detail.max = data.dates.at(-1);
      if (!data.dates.includes(detail.value)) detail.value = detail.max;
      render(); $('analytics-content').hidden = false;
      $('analytics-status').textContent = data.scope === 'own_crews' ? 'Показаны назначения доступных вам бригад.' : data.scope === 'assigned_smu' ? 'Показаны назначения сотрудников доступных вам СМУ.' : 'Данные по действующим назначениям за выбранный период.';
    } catch (error) {
      if (current !== requestId) return;
      data = null; $('analytics-status').textContent = error.message; $('analytics-status').classList.add('error-text');
    }
  }
  const initial = new Date($('analytics-end').value + 'T12:00:00Z');
  initial.setUTCDate(initial.getUTCDate() - 13); $('analytics-start').value = initial.toISOString().slice(0, 10);
  $('analytics-form').addEventListener('submit', event => { event.preventDefault(); load(); });
  $('analytics-user').addEventListener('change', load);
  $('analytics-detail-date').addEventListener('change', () => {
    if (!data) return;
    if (!data.dates.includes($('analytics-detail-date').value)) $('analytics-detail-date').value = data.dates.at(-1);
    render();
  });
  for (const id of ['analytics-start', 'analytics-end']) $(id).addEventListener('change', () => {
    ++requestId; data = null; $('analytics-content').hidden = true;
    $('analytics-status').textContent = 'Нажмите «Показать», чтобы обновить период.';
  });
  window.personnelDashboard = {load};
  if ($('user-activity-users')) {
    new MutationObserver(() => {
      $('analytics-activity-count').textContent = number($('user-activity-users').querySelectorAll('tbody tr').length);
    }).observe($('user-activity-users'), {childList: true});
  }
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if ($('view-analytics').classList.contains('active')) render(); }, 100);
  });
})();
