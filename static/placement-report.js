(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const root = $('placement-report');
  if (!root) return;
  const labels = {assigned: 'Расставлены', unassigned: 'Не расставлены', absent: 'Неявка'};
  let sequence = 0, readyQuery = null, downloading = false;
  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    Object.assign(node, attrs);
    node.append(...children);
    return node;
  };
  const setStatus = (text, error = false) => {
    $('placement-report-status').textContent = text;
    $('placement-report-status').classList.toggle('error-text', error);
  };
  function updateOptions(id, values, all, empty) {
    const input = $(id), selected = input.value;
    if (selected && !values.includes(JSON.parse(selected))) values = [...values, JSON.parse(selected)];
    input.replaceChildren(el('option', {value: ''}, all),
      ...values.map(value => el('option', {value: JSON.stringify(value)}, value || empty)));
    input.value = selected;
  }
  function peopleList(people) {
    const list = el('ul', {className: 'placement-report-people'});
    people.forEach(person => {
      const assignments = person.assignments.map(a => `${a.shift}: ${a.object_name} / ${a.subobject_name}`);
      list.append(el('li', {}, el('div', {}, el('strong', {}, person.full_name),
        el('small', {}, `Таб. № ${person.personnel_no || 'не указан'} · ${person.profession || 'Профессия не указана'}`),
        el('small', {}, person.department || 'СМУ не указано')),
      el('div', {}, el('span', {}, assignments.join('; ') || 'Назначения нет'),
        ...(person.status === 'absent' ? [el('small', {className: 'placement-report-absence'}, person.attendance_status)] : []))));
    });
    return list;
  }
  function render(data) {
    $('placement-report-totals').replaceChildren(...Object.entries({total: 'Всего сотрудников', ...labels})
      .map(([key, label]) => el('div', {className: 'placement-report-stat ' + key},
        el('span', {}, label), el('strong', {}, String(data.totals[key])))));
    $('placement-report-note').textContent = data.note;
    const target = $('placement-report-groups'); target.replaceChildren();
    if (!data.groups.length) { target.append(el('p', {className: 'empty-state'}, 'По выбранным условиям сотрудников нет.')); return; }
    let pps = null, section;
    data.groups.forEach(group => {
      if (pps !== group.pps) {
        pps = group.pps;
        section = el('section', {className: 'placement-report-pps'}, el('h2', {}, pps || 'Без ППС'));
        target.append(section);
      }
      const block = el('section', {className: 'placement-report-category'},
        el('h3', {}, group.category || 'Без категории', el('span', {}, 'Всего: ' + group.total)));
      Object.entries(labels).forEach(([key, label]) => {
        const detail = el('details', {className: 'placement-report-line ' + key},
          el('summary', {}, el('span', {}, label), el('strong', {}, String(group[key]))));
        let populated = false;
        detail.addEventListener('toggle', () => {
          if (!detail.open || populated) return;
          populated = true;
          detail.append(group[key] ? peopleList(group.people.filter(person => person.status === key))
            : el('p', {className: 'placement-report-empty'}, 'Сотрудников нет.'));
        });
        block.append(detail);
      });
      section.append(block);
    });
  }
  async function load() {
    const id = ++sequence;
    readyQuery = null; $('placement-report-pdf').disabled = true;
    $('placement-report-totals').replaceChildren(); $('placement-report-groups').replaceChildren();
    $('placement-report-note').textContent = '';
    if (!$('placement-report-date').value) { setStatus('Выберите дату отчёта.', true); return; }
    const query = new URLSearchParams({date: $('placement-report-date').value});
    for (const key of ['pps', 'category']) {
      const value = $('placement-report-' + key).value;
      if (value) query.set(key, JSON.parse(value));
    }
    setStatus('Загрузка отчёта…');
    try {
      const response = await fetch('/api/placement-report?' + query, {cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Не удалось загрузить отчёт.');
      if (id !== sequence) return;
      updateOptions('placement-report-pps', data.options.pps, 'Все ППС', 'Без ППС');
      updateOptions('placement-report-category', data.options.categories, 'Все категории', 'Без категории');
      render(data); readyQuery = query.toString();
      $('placement-report-pdf').disabled = downloading;
      setStatus('Отчёт обновлён. Нажмите строку статуса, чтобы увидеть ФИО.');
    } catch (error) { if (id === sequence) setStatus(error.message, true); }
  }
  $('placement-report-filters').addEventListener('submit', event => { event.preventDefault(); load(); });
  for (const key of ['date', 'pps', 'category']) $('placement-report-' + key).addEventListener('change', load);
  $('placement-report-pdf').addEventListener('click', async () => {
    if (readyQuery === null || downloading) return;
    const params = new URLSearchParams(readyQuery);
    params.set('details', $('placement-report-pdf-details').checked ? '1' : '0');
    const query = params.toString(), id = sequence;
    downloading = true; $('placement-report-pdf').disabled = true;
    setStatus('Подготовка PDF…');
    try {
      const response = await fetch('/api/placement-report/pdf?' + query, {cache: 'no-store'});
      if (!response.ok) {
        const data = await response.json(); throw new Error(data.error || 'Не удалось подготовить PDF.');
      }
      if (!response.headers.get('Content-Type')?.includes('application/pdf')) throw new Error('Сервер не вернул PDF. Обновите страницу.');
      const blob = await response.blob(), url = URL.createObjectURL(blob);
      const a = el('a', {href: url, download: 'placement-report-' + new URLSearchParams(query).get('date') + '.pdf'});
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      if (id === sequence) setStatus('PDF подготовлен с выбранными фильтрами' + (params.get('details') === '1' ? ' и списками ФИО.' : '.'));
    } catch (error) { if (id === sequence) setStatus(error.message, true); }
    finally { downloading = false; $('placement-report-pdf').disabled = readyQuery === null; }
  });
  window.placementReport = {load};
})();
