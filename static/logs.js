(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('view-logs')) return;
  const actions = {assign: 'Назначение', move: 'Перенос', clear: 'Снятие', update: 'Обновление', employee_delete: 'Удаление сотрудника', employee_restore: 'Восстановление сотрудника'};
  let page = 1, pages = 1, request = 0, busy = false;
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const date = value => value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value.split('-').reverse().join('.') : value || '—';
  function changedAt(value) {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? 'Время не сохранено' : parsed.toLocaleString('ru-RU', {timeZone: 'Europe/Moscow'});
  }
  function site(row, prefix) {
    const box = node('div');
    box.append(node('strong', row[prefix + '_name'] || (row[prefix + '_subobject_id'] == null ? 'Без назначения' : 'Подобъект не найден')));
    if (row[prefix + '_group']) box.append(node('small', row[prefix + '_group']));
    return box;
  }
  function render(rows) {
    if (!rows.length) { $('logs-list').replaceChildren(node('p', 'Событий по выбранным условиям нет.', 'empty-state')); return; }
    const headers = ['Время (МСК)', 'Пользователь', 'Действие', 'Сотрудник', 'Дата / смена', 'Было', 'Стало', 'Причина'];
    const table = node('table', null, 'logs-table');
    const head = node('thead'), heading = node('tr'), body = node('tbody');
    headers.forEach(text => { const th = node('th', text); th.scope = 'col'; heading.append(th); });
    head.append(heading);
    for (const row of rows) {
      const actor = node('div'); actor.append(node('strong', row.actor_name || 'Пользователь не найден'), node('small', row.actor_username || ''));
      const worker = node('div'); worker.append(node('strong', row.worker_name || 'Сотрудник не найден'), node('small', 'Таб. № ' + (row.personnel_no || '—')), node('small', row.crew_name || 'Бригада не найдена'));
      const work = node('div'); work.append(node('strong', date(row.work_date)), node('small', row.shift === '1 смена' ? 'День' : ['2 смена', 'Ночная смена'].includes(row.shift) ? 'Ночь' : row.shift));
      const tr = node('tr'); tr.dataset.eventId = row.id;
      if (row.action === 'employee_delete') work.append(node('small', 'Дата увольнения'));
      if (row.action === 'employee_restore') work.append(node('small', 'Дата восстановления'));
      [changedAt(row.changed_at), actor, actions[row.action], worker, work,
        row.action === 'employee_delete' ? 'Действующий сотрудник' : row.action === 'employee_restore' ? 'Отключён' : site(row, 'before'),
        row.action === 'employee_delete' ? 'Удалён из действующего состава' : row.action === 'employee_restore' ? 'Действующий сотрудник' : site(row, 'after'), row.reason || '—'].forEach((value, index) => {
        const td = node('td'); td.dataset.label = headers[index]; td.append(value); tr.append(td);
      });
      body.append(tr);
    }
    table.append(head, body); $('logs-list').replaceChildren(table);
  }
  async function load(nextPage = 1) {
    const current = ++request;
    busy = true; $('logs-form').inert = true;
    $('logs-prev').disabled = true; $('logs-next').disabled = true;
    $('logs-status').classList.remove('error-text'); $('logs-status').textContent = 'Загрузка журнала…';
    const query = new URLSearchParams(new FormData($('logs-form')));
    query.set('page', nextPage);
    try {
      const response = await fetch('/api/logs?' + query, {cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Не удалось загрузить журнал.');
      if (current !== request) return;
      const select = $('logs-actor'), selected = select.value;
      select.replaceChildren(new Option('Все пользователи', ''), ...data.actors.map(actor => new Option(actor.full_name + ' · ' + actor.username, String(actor.id))));
      select.value = selected;
      page = data.page; pages = data.pages; render(data.rows);
      $('logs-status').textContent = 'Найдено событий: ' + data.total.toLocaleString('ru-RU');
      $('logs-page').textContent = 'Страница ' + page + ' из ' + pages;
    } catch (error) {
      if (current !== request) return;
      page = pages = 1; $('logs-list').replaceChildren(); $('logs-page').textContent = '';
      $('logs-status').textContent = error.message; $('logs-status').classList.add('error-text');
    } finally {
      if (current === request) {
        busy = false; $('logs-form').inert = false;
        $('logs-prev').disabled = page <= 1; $('logs-next').disabled = page >= pages;
      }
    }
  }
  $('logs-form').addEventListener('submit', event => { event.preventDefault(); if (!busy) load(); });
  $('logs-reset').addEventListener('click', () => { if (busy) return; $('logs-form').reset(); load(); });
  $('logs-prev').addEventListener('click', () => { if (!busy && page > 1) load(page - 1); });
  $('logs-next').addEventListener('click', () => { if (!busy && page < pages) load(page + 1); });
  window.logsScreen = {load};
})();
