(() => {
  'use strict';
  const MF = window.MultiFilter;
  const $ = id => document.getElementById(id);
  const root = $('placement-report');
  if (!root) return;
  ['placement-report-pps','placement-report-category','placement-report-author'].forEach(id => MF.enable($(id)));
  const labels = {assigned: 'Расставлены', unassigned: 'Не расставлены', absent: 'Неявка'};
  let sequence = 0, readyQuery = null, downloading = false, mode = 'placement';
  let currentData = null, authorLabels = {};
  const humanDate = day => day.split('-').reverse().join('.');
  const initialStart = new Date($('placement-report-date').value + 'T12:00:00Z');
  initialStart.setUTCDate(initialStart.getUTCDate() - 6);
  $('placement-report-start').value = initialStart.toISOString().slice(0, 10);
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
  function updateOptions(id, values, all, empty, names = null) {
    const input = $(id), selected = MF.get(input);
    MF.values(selected).map(value => JSON.parse(value)).forEach(value => {if(!values.includes(value))values.push(value);});
    input.replaceChildren(el('option', {value: ''}, all),
      ...values.map(value => el('option', {value: JSON.stringify(value)}, names ? (names[value] || 'Пользователь №' + value) : value || empty)));
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
        el('span', {}, label + (data.dynamics ? ' на ' + humanDate(data.date) : '')), el('strong', {}, String(data.totals[key])))));
    $('placement-report-note').textContent = data.note;
    const target = $('placement-report-groups'); target.replaceChildren();
    if (mode === 'category') { window.categoryPlacement.render(data, target, peopleList); return; }
    if (data.dynamics) { renderDynamics(data, target); return; }
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

  function renderDynamics(data, target) {
    const trend = data.dynamics, metric = $('placement-report-metric').value;
    const metricLabel = ({...labels, total: 'Всего сотрудников'})[metric];
    const fullPps = $('placement-report-pps-details').checked;
    target.append(el('h2', {}, 'Динамика по категориям ГДЛР'),
      el('p', {className: 'table-note'}, humanDate(trend.start) + ' — ' + humanDate(trend.end) +
        ' · ' + metricLabel + ', чел. Изменение: ' + humanDate(trend.end) + ' минус ' + humanDate(trend.comparison_date) + '. Нажмите категорию для раскрытия ППС, число — для списка ФИО на эту дату.'));
    if (!trend.categories.length) { target.append(el('p', {className: 'empty-state'}, 'По выбранным условиям сотрудников нет.')); return; }
    const scroller = el('div', {className: 'placement-report-matrix-scroll placement-dynamics-scroll', tabIndex: 0});
    scroller.setAttribute('role', 'region'); scroller.setAttribute('aria-label', 'Динамика по категориям и ППС, ' + metricLabel);
    const table = el('table', {className: 'placement-report-matrix placement-dynamics-table'});
    table.append(el('thead', {}, el('tr', {}, ...['Категория ГДЛР / ППС', ...trend.dates.map(humanDate), 'Изменение за день, чел.']
      .map(text => el('th', {scope: 'col'}, text)))));
    const body = el('tbody'), details = el('section', {className: 'placement-report-selected', hidden: true, tabIndex: -1});
    let detailRequest = 0;
    const renderSequence = sequence;
    async function drill(day, category, pps, button) {
      const id = ++detailRequest;
      const query = new URLSearchParams(readyQuery);
      for (const key of ['start', 'metric', 'pps_details']) query.delete(key);
      query.set('date', day);
      if (category !== undefined) query.set('category', category);
      if (pps !== undefined) query.set('pps', pps);
      details.hidden = false; details.replaceChildren(el('p', {}, 'Загрузка сотрудников…'));
      try {
        const response = await fetch('/api/placement-report?' + query, {cache: 'no-store'});
        const snapshot = await response.json();
        if (!response.ok) throw new Error(snapshot.error || 'Не удалось загрузить сотрудников.');
        if (id !== detailRequest || renderSequence !== sequence || !details.isConnected) return;
        const people = snapshot.groups.flatMap(group => group.people).filter(person => metric === 'total' || person.status === metric);
        const close = el('button', {type: 'button', className: 'secondary-button'}, 'Закрыть список');
        close.addEventListener('click', () => { detailRequest++; details.hidden = true; button.focus({preventScroll: true}); });
        details.replaceChildren(el('h3', {}, [humanDate(day), category === undefined ? 'Все категории' : category || 'Без категории',
          pps === undefined ? 'Выбранные ППС' : pps || 'Без ППС', metricLabel + ': ' + people.length].join(' · ')), close, peopleList(people));
        details.focus({preventScroll: true}); details.scrollIntoView({block: 'nearest'});
      } catch (error) { if (id === detailRequest && renderSequence === sequence && details.isConnected) details.replaceChildren(el('p', {className: 'error-text'}, error.message)); }
    }
    function row(title, counts, changes, category, pps, className = '') {
      const tr = el('tr', {className}, el('th', {scope: 'row'}, title));
      trend.dates.forEach((day, index) => {
        const value = counts[metric][index], td = el('td', {});
        if (value) {
          const button = el('button', {type: 'button', className: 'report-fact-link'}, String(value));
          button.setAttribute('aria-label', [category === undefined ? 'Все категории' : category || 'Без категории', pps || '', humanDate(day), metricLabel, value].join(', '));
          button.addEventListener('click', () => drill(day, category, pps, button)); td.append(button);
        } else td.textContent = '0';
        tr.append(td);
      });
      const change = changes[metric];
      const changeCell = el('td', {className: 'placement-dynamics-change'});
      const changeText = (change > 0 ? '+' : '') + change;
      if (document.getElementById('view-staffing')) {
        const button = el('button', {type: 'button', className: 'report-fact-link'}, changeText);
        button.setAttribute('aria-label', 'Показать, кто пришёл и ушёл: ' + (category === undefined ? 'Все категории' : category || 'Без категории') + (pps === undefined ? '' : ' / ' + (pps || 'Без ППС')));
        button.title = 'Открыть изменения состава в расстановке';
        button.addEventListener('click', () => {
          const query = new URLSearchParams(readyQuery);
          window.openStaffingReport({kind: 'changes', date: trend.end, shift: '', metric,
            author: query.has('author') ? query.getAll('author') : undefined,
            category: category === undefined ? (query.has('category') ? query.getAll('category') : undefined) : category,
            pps: pps === undefined ? (query.has('pps') ? query.getAll('pps') : undefined) : pps,
            label: metricLabel + ' · ' + (category === undefined ? 'Все выбранные категории' : category || 'Без категории') + (pps === undefined ? '' : ' · ' + (pps || 'Без ППС'))});
        });
        changeCell.append(button);
      } else changeCell.textContent = changeText;
      tr.append(changeCell);
      body.append(tr); return tr;
    }
    trend.categories.forEach((category, index) => {
      const button = el('button', {type: 'button', className: 'placement-category-toggle'});
      const title = category.category || 'Без категории';
      row(button, category.counts, category.changes, category.category, undefined, 'placement-dynamics-category');
      const children = category.pps.map(item => row(item.pps || 'Без ППС', item.counts, item.changes, category.category, item.pps, 'placement-dynamics-pps'));
      children.forEach((tr, childIndex) => { tr.id = 'placement-dynamics-' + index + '-' + childIndex; });
      button.setAttribute('aria-controls', children.map(tr => tr.id).join(' '));
      function expand(open) { button.textContent = (open ? '▾ ' : '▸ ') + title; button.setAttribute('aria-expanded', String(open)); children.forEach(tr => { tr.hidden = !open; }); }
      button.addEventListener('click', () => expand(button.getAttribute('aria-expanded') !== 'true'));
      expand(fullPps);
    });
    row('Итого', trend.totals, trend.changes, undefined, undefined, 'placement-report-matrix-total');
    table.append(body); scroller.append(table); target.append(scroller, details);
  }

  async function load() {
    const id = ++sequence;
    readyQuery = null; currentData = null; $('placement-report-pdf').disabled = true;
    $('placement-report-totals').replaceChildren(); $('placement-report-groups').replaceChildren();
    $('placement-report-note').textContent = '';
    if (!$('placement-report-date').value) { setStatus('Выберите дату отчёта.', true); return; }
    const query = new URLSearchParams({date: $('placement-report-date').value});
    if (mode !== 'category') {
      if (!$('placement-report-start').value) { setStatus('Выберите начало периода.', true); return; }
      query.set('start', $('placement-report-start').value);
      query.set('metric', $('placement-report-metric').value);
    }
    for (const key of ['pps', 'category', 'author']) {
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
      Object.assign(authorLabels, data.options.author_labels);
      updateOptions('placement-report-author', data.options.authors, 'Все авторы', 'Автор не определён', authorLabels);
      currentData = data; render(data); readyQuery = query.toString();
      $('placement-report-pdf').disabled = downloading;
      setStatus('Отчёт обновлён. Нажмите число в таблице, чтобы увидеть ФИО.');
    } catch (error) { if (id === sequence) setStatus(error.message, true); }
  }
  $('placement-report-filters').addEventListener('submit', event => { event.preventDefault(); load(); });
  for (const key of ['start', 'date', 'pps', 'category', 'author']) $('placement-report-' + key).addEventListener('change', load);
  for (const key of ['metric', 'pps-details']) $('placement-report-' + key).addEventListener('change', () => {
    if (currentData) render(currentData);
  });
  async function queuedPdf(params) {
    const root = document.querySelector('.app-shell');
    const storageKey = 'placement-pdf:' + root.dataset.userId + ':' + params.toString();
    let key; try { key = sessionStorage.getItem(storageKey); } catch (_) {}
    if (!key) { key = crypto.randomUUID(); try { sessionStorage.setItem(storageKey, key); } catch (_) {} }
    const body = {type: 'placement_pdf', date: params.get('date'),
      filters: [...params.entries()].filter(([name]) => name !== 'date'), request_key: key};
    const started = await fetch('/api/workforce/export-jobs', {method:'POST', cache:'no-store',
      headers:{'Content-Type':'application/json','X-CSRF-Token':root.dataset.csrf},body:JSON.stringify(body)});
    const job = await started.json().catch(() => ({}));
    if (!started.ok) { if (started.status < 500) try { sessionStorage.removeItem(storageKey); } catch (_) {} throw new Error(job.error || 'Не удалось поставить PDF в очередь.'); }
    for (let attempt = 0; attempt < 180; attempt++) {
      const stateResponse = await fetch('/api/workforce/export-jobs/' + key, {cache:'no-store'});
      const state = await stateResponse.json().catch(() => ({}));
      if (!stateResponse.ok || state.state === 'failed') { try { sessionStorage.removeItem(storageKey); } catch (_) {} throw new Error(state.error || 'Не удалось подготовить PDF.'); }
      if (state.state === 'ready') {
        const file = await fetch('/api/workforce/export-jobs/' + key + '/download', {cache:'no-store'});
        if (file.ok) try { sessionStorage.removeItem(storageKey); } catch (_) {}
        return file;
      }
      setStatus(state.state === 'pending' ? 'PDF в очереди…' : 'Формируем PDF…');
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    throw new Error('PDF ещё формируется. Нажмите кнопку повторно, чтобы проверить готовность.');
  }

  $('placement-report-pdf').addEventListener('click', async () => {
    if (readyQuery === null || downloading) return;
    const params = new URLSearchParams(readyQuery);
    params.set('details', $('placement-report-pdf-details').checked ? '1' : '0');
    if (mode !== 'category') {
      params.set('metric', $('placement-report-metric').value);
      params.set('pps_details', $('placement-report-pps-details').checked ? '1' : '0');
    }
    const query = params.toString(), id = sequence;
    downloading = true; $('placement-report-pdf').disabled = true;
    setStatus('Подготовка PDF…');
    try {
      const response = document.body.classList.contains('workforce-layout') ? await queuedPdf(params) : await fetch('/api/placement-report/pdf?' + query, {cache: 'no-store'});
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
    $('placement-report-date-label').textContent = mode === 'category' ? 'Дата отчёта' : 'По дату';
  }};
})();
