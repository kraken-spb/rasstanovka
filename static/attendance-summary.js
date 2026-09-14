(() => {
  'use strict';
  const statuses = ['Вых', 'Без сод', 'Больн', 'МО'];
  const normalize = value => String(value || '').toLocaleLowerCase('ru').replace(/ё/g, 'е');
  const element = (tag, text, className) => {
    const item = document.createElement(tag);
    if (text != null) item.textContent = text;
    if (className) item.className = className;
    return item;
  };
  function render(container, data, query) {
    const words = normalize(query).split(/\s+/).filter(Boolean);
    const rows = data.absences.filter(row => words.every(word => normalize((row.object_names || '') + ' ' + (row.subobject_names || '')).includes(word)));
    container.replaceChildren(element('h2', 'Неявки'));
    container.append(element('p', 'Отдельно от рабочей численности. Каждый сотрудник учитывается один раз за дату. Выбранные дата, категория ГДЛР и поиск по объектам действуют на этот блок.', 'table-note'));
    if (!rows.length) { container.append(element('p', 'По текущим условиям неявок нет.')); return; }
    const groups = new Map(), totals = [0, 0, 0, 0];
    rows.forEach(row => {
      const key = JSON.stringify([row.work_date, row.employer]);
      if (!groups.has(key)) groups.set(key, {day: row.work_date, employer: row.employer, counts: [0, 0, 0, 0]});
      const index = statuses.indexOf(row.status);
      if (index < 0) throw new Error('Неизвестный статус неявки. Обновите страницу.');
      groups.get(key).counts[index]++; totals[index]++;
    });
    const table = element('table', null, 'attendance-summary-table');
    const head = element('thead'), header = element('tr');
    ['Дата / работодатель', ...statuses, 'Итого'].forEach(text => header.append(element('th', text)));
    head.append(header); table.append(head);
    const body = element('tbody');
    const append = (label, counts, isTotal = false) => {
      const tr = element('tr', null, isTotal ? 'report-total' : '');
      const th = element('th', label); th.scope = 'row'; tr.append(th);
      [...counts, counts.reduce((a, b) => a + b, 0)].forEach(count => tr.append(element('td', String(count))));
      body.append(tr);
    };
    [...groups.values()].sort((a, b) => a.day.localeCompare(b.day) || a.employer.localeCompare(b.employer, 'ru')).forEach(group =>
      append((data.dates.length > 1 ? group.day.split('-').reverse().join('.') + ' · ' : '') + (group.employer || 'Организация не указана'), group.counts));
    append(data.dates.length > 1 ? 'Итого за период, чел.-дней' : 'Итого, чел.', totals, true);
    table.append(body); container.append(table);
    const details = element('details', null, 'attendance-people');
    details.append(element('summary', 'Список сотрудников · ' + rows.length + (data.dates.length > 1 ? ' записей' : ' чел.')));
    const list = element('ul');
    rows.forEach(row => {
      const li = element('li');
      li.append(element('strong', row.full_name), element('span', row.status, 'attendance-status-badge'));
      li.append(element('small', [row.work_date.split('-').reverse().join('.'), 'Таб. № ' + row.personnel_no,
        row.employer || 'Организация не указана', row.crew_name || 'Без бригады', row.category,
        row.object_names, row.subobject_names].filter(Boolean).join(' · ')));
      list.append(li);
    });
    details.append(list); container.append(details);
  }
  window.attendanceSummary = {render};
})();
