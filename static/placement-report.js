(() => {
  'use strict';
  const MF = window.MultiFilter;
  const $ = id => document.getElementById(id);
  const root = $('placement-report');
  if (!root) return;
  ['placement-report-pps','placement-report-category'].forEach(id => MF.enable($(id)));
  const labels = {assigned: 'Расставлены', unassigned: 'Не расставлены', absent: 'Неявка'};
  let sequence = 0, readyQuery = null, downloading = false, mode = 'placement';
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
    const input = $(id), selected = MF.get(input);
    MF.values(selected).map(value => JSON.parse(value)).forEach(value => {if(!values.includes(value))values.push(value);});
    input.replaceChildren(el('option', {value: ''}, all),
      ...values.map(value => el('option', {value: JSON.stringify(value)}, value || empty)));
    MF.set(input, selected);
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
    if (mode === 'category') { window.categoryPlacement.render(data, target, peopleList); return; }
    if (!data.groups.length) { target.append(el('p', {className: 'empty-state'}, 'По выбранным условиям сотрудников нет.')); return; }
    const scroller = el('div', {className: 'placement-report-matrix-scroll', tabIndex: 0});
    scroller.setAttribute('role', 'region'); scroller.setAttribute('aria-label', 'Отчёт по компаниям-подрядчикам');
    const table = el('table', {className: 'placement-report-matrix'});
    const companies = data.contractors;
    const head = el('tr', {}, ...['ППС', 'Категория ГДЛР', 'Статус', ...companies.map(c => c || 'Подрядчик не указан'), 'Общий итог']
      .map(label => el('th', {scope: 'col'}, label)));
    table.append(el('thead', {}, head));
    const body = el('tbody'), details = el('section', {className: 'placement-report-selected', hidden: true});
    const append = (group, key, label) => {
      const row = el('tr', {}, el('th', {scope: 'row'}, group.pps || 'Без ППС'), el('th', {scope: 'row'}, group.category || 'Без категории'), el('th', {scope: 'row'}, label));
      [...companies, undefined].forEach(company => {
        const count = company === undefined ? group[key] : group.companies[company]?.[key] || 0;
        const cell = el('td', {});
        if (count) {
          const button = el('button', {type: 'button', className: 'report-fact-link'}, String(count));
          button.setAttribute('aria-label', [group.pps, group.category, label, company === undefined ? 'Все подрядчики' : company || 'Подрядчик не указан', count].join(', '));
          button.addEventListener('click', () => {
            details.hidden = false;
            details.replaceChildren(el('h3', {}, [group.pps, group.category, label, company === undefined ? 'Все подрядчики' : company || 'Подрядчик не указан'].filter(Boolean).join(' · ')),
              peopleList(group.people.filter(p => (key === 'total' || p.status === key) && (company === undefined || p.contractor === company))));
          });
          cell.append(button);
        } else cell.textContent = '0';
        row.append(cell);
      });
      if (key === 'total') row.className = 'placement-report-matrix-total';
      body.append(row);
    };
    data.groups.forEach(group => Object.entries({...labels, total: 'Всего'}).forEach(([key,label]) => append(group,key,label)));
    const totals = el('tr', {className: 'placement-report-matrix-total'}, el('th', {colSpan: 3, scope: 'row'}, 'Итого'));
    companies.forEach(company => totals.append(el('td', {}, String(data.groups.reduce((sum, group) => sum + (group.companies[company]?.total || 0), 0)))));
    totals.append(el('td', {}, String(data.totals.total))); body.append(totals);
    table.append(body); scroller.append(table); target.append(scroller, details);
  }

  async function load() {
    const id = ++sequence;
    readyQuery = null; $('placement-report-pdf').disabled = true;
    $('placement-report-totals').replaceChildren(); $('placement-report-groups').replaceChildren();
    $('placement-report-note').textContent = '';
    if (!$('placement-report-date').value) { setStatus('Выберите дату отчёта.', true); return; }
    const query = new URLSearchParams({date: $('placement-report-date').value});
    for (const key of ['pps', 'category']) {
      const value = MF.get($('placement-report-' + key));
      MF.params(query, key, value, value => JSON.parse(value));
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
      setStatus('Отчёт обновлён. Нажмите число в таблице, чтобы увидеть ФИО.');
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
  window.placementReport = {load, setMode(value) {
    mode = value; root.classList.toggle('category-mode', mode === 'category');
  }};
})();
