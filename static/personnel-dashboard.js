(() => {
  'use strict';
  const MF = window.MultiFilter;
  const $ = id => document.getElementById(id);
  if (!$('view-analytics')) return;
  let data = null, requestId = 0;
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
  MF.enable($('analytics-user'));
  function render() {
    if (!data) return;
    const chosen = MF.get($('analytics-user'));
    const user = data.users.find(item => item.id === chosen);
    const counts = user ? user.counts : data.counts;
    const unique = user ? user.unique_count : data.unique_count;
    const last = counts.at(-1), change = last - counts[0];
    const metrics = [['На конец периода', number(last)], ['Изменение за период', delta(change)],
      ['Уникальных за период', number(unique)], ['Авторов с назначениями', number(data.users.filter(item => item.unique_count && item.id !== 'unknown' && MF.matches(chosen,item.id)).length)]];
    $('analytics-stats').replaceChildren(...metrics.map(([label, value]) => {
      const item = node('div', null, 'analytics-metric'); item.append(node('span', label), node('strong', value)); return item;
    }));
    $('analytics-chart-title').textContent = user ? user.full_name : chosen ? 'Персонал выбранных пользователей' : 'Весь расставленный персонал';
    $('analytics-period').textContent = human(data.dates[0]) + ' — ' + human(data.dates.at(-1));
    $('analytics-chart').replaceChildren(chart(counts, data.dates));
    $('analytics-chart-note').textContent = unique ? 'Человек на каждую дату. Точные значения доступны в подсказках точек графика.' : 'За выбранный период назначений нет.';
    const selectedDate = $('analytics-detail-date').value;
    const dayIndex = data.dates.indexOf(selectedDate);
    const headers = ['Пользователь', human(selectedDate), 'Изменение', 'Динамика', 'Уникальных за период'];
    const table = node('table', null, 'analytics-users-table');
    const head = node('thead'), heading = node('tr');
    headers.forEach(label => { const th = node('th', label); th.scope = 'col'; heading.append(th); });
    head.append(heading); table.append(head);
    const body = node('tbody');
    const users = data.users.filter(item => MF.matches(chosen,item.id)).slice().sort((a, b) => b.counts[dayIndex] - a.counts[dayIndex] || b.unique_count - a.unique_count || a.full_name.localeCompare(b.full_name, 'ru'));
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
      $('analytics-status').textContent = data.scope === 'own_crews' ? 'Показаны назначения доступных вам бригад.' : 'Данные по действующим назначениям за выбранный период.';
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
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if ($('view-analytics').classList.contains('active')) render(); }, 100);
  });
})();
