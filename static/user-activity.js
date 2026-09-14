(() => {
  'use strict';
  const root = document.querySelector('.app-shell');
  if (!root) return;
  const $ = id => document.getElementById(id);
  const panel = $('user-activity-panel');
  let heartbeatRequest = null, requestId = 0;
  const visible = () => panel && $('view-analytics').classList.contains('active') && !document.hidden;
  const heartbeat = () => {
    if (document.hidden) return Promise.resolve();
    if (!heartbeatRequest) {
      heartbeatRequest = fetch('/api/session/heartbeat', {
        method: 'POST', cache: 'no-store', headers: {'X-CSRF-Token': root.dataset.csrf},
      }).catch(() => null).finally(() => { heartbeatRequest = null; });
    }
    return heartbeatRequest;
  };
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const dateTime = value => value ? new Intl.DateTimeFormat('ru-RU', {
    timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date(value)) : 'Нет данных';
  const number = value => value == null ? '—' : value.toLocaleString('ru-RU');
  function render(data) {
    $('user-activity-stats').replaceChildren(...[
      ['Входов за дату', data.login_count], ['Вошли за дату', data.users_logged_in], ['Онлайн сейчас', data.online_count],
    ].map(([label, count]) => {
      const card = node('div', null, 'analytics-metric'); card.append(node('span', label), node('strong', number(count))); return card;
    }));
    const headers = ['Пользователь', 'Входов за дату', 'Статус сейчас', 'Последний вход (МСК)'];
    const table = node('table', null, 'activity-users-table');
    const heading = node('tr'), head = node('thead');
    headers.forEach(label => { const th = node('th', label); th.scope = 'col'; heading.append(th); });
    head.append(heading); table.append(head);
    const body = node('tbody');
    for (const user of data.rows) {
      const row = node('tr');
      const name = node('div', null, 'activity-user-name');
      name.append(node('strong', user.full_name), node('small', user.username + (user.active ? '' : ' · учётная запись отключена')));
      const status = node('span', user.online ? 'Онлайн' : 'Не в сети', 'activity-presence' + (user.online ? ' is-online' : ''));
      [name, number(user.login_count), status, dateTime(user.last_login_at)].forEach((value, index) => {
        const cell = node('td'); cell.dataset.label = headers[index]; cell.append(value); row.append(cell);
      });
      body.append(row);
    }
    table.append(body); $('user-activity-users').replaceChildren(table);
    const beginning = 'Учёт входов ведётся с ' + dateTime(data.tracking_started_at) + ' МСК.';
    $('user-activity-note').textContent = beginning + (data.coverage === 'unavailable'
      ? ' За выбранную дату данных нет.' : data.coverage === 'partial' ? ' За эту дату показаны входы только после начала учёта.' : '');
    $('user-activity-status').textContent = 'Обновлено: ' + dateTime(data.as_of) + ' МСК. Автообновление — раз в минуту.';
  }
  async function load() {
    if (!panel) return;
    const current = ++requestId, selected = $('user-activity-date').value;
    const status = $('user-activity-status'); status.textContent = 'Загрузка активности…'; status.classList.remove('error-text');
    try {
      await heartbeat();
      const response = await fetch('/api/user-activity?' + new URLSearchParams({date: selected}), {cache: 'no-store'});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Не удалось загрузить активность.');
      if (current !== requestId) return;
      render(result);
    } catch (error) {
      if (current !== requestId) return;
      $('user-activity-stats').replaceChildren(); $('user-activity-users').replaceChildren(); $('user-activity-note').textContent = '';
      status.textContent = error.message; status.classList.add('error-text');
    }
  }
  if (panel) {
    const parts = new Intl.DateTimeFormat('en-CA', {timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(new Date());
    const part = name => parts.find(item => item.type === name).value;
    $('user-activity-date').value = `${part('year')}-${part('month')}-${part('day')}`;
    $('user-activity-form').addEventListener('submit', event => { event.preventDefault(); load(); });
    $('user-activity-date').addEventListener('change', load);
    window.userActivityDashboard = {load};
  }
  const refresh = () => { if (!document.hidden) { if (visible()) load(); else heartbeat(); } };
  refresh();
  setInterval(refresh, 60000);
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('pageshow', refresh);
})();
