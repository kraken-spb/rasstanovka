(() => {
  'use strict';
  const $ = id => document.getElementById('outstaff-' + id);
  if (!$('import-dialog')) return;
  const state = {columns: [], conditions: [], decisions: {categories: {}, departments: {}, identities: {}}, preview: null, busy: false, page: 0};
  const el = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    Object.entries(props).forEach(([key, value]) => {
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key in node) node[key] = value;
      else node.setAttribute(key, value);
    });
    children.flat().forEach(child => node.append(child)); return node;
  };
  const option = (value, label) => el('option', {value: String(value)}, label || '(пусто)');
  const activeFilters = () => state.conditions.filter(condition => condition.op);
  function invalidate() { if (state.preview) state.preview.token = null; $('apply').disabled = true; }
  function filtersChanged() {
    state.preview = null; state.page = 0; invalidate();
    $('mapping').replaceChildren(); $('preview-list').replaceChildren(); $('preview-page').textContent = '';
    $('preview-prev').disabled = $('preview-next').disabled = true;
    $('import-error').textContent = '';
    $('import-status').textContent = 'Фильтры изменены. Нажмите «Проверить и сопоставить», чтобы увидеть отобранных сотрудников.';
    filterInfo();
  }
  function filterInfo() {
    const count = activeFilters().length;
    $('filter-info').textContent = !state.columns.length ? 'Выберите файл — здесь появится фильтр для каждого столбца.'
      : count ? 'Столбцов с фильтром: ' + count + '. Между столбцами — «И», внутри списка значений — «ИЛИ».'
        : 'Без фильтров: будут выбраны все строки файла. Выберите условие рядом с нужным столбцом.';
    $('filter-reset').disabled = state.busy || !count;
  }
  function busy(value) {
    state.busy = value;
    for (const node of $('import-dialog').querySelectorAll('button,input,select')) node.disabled = value;
    if (!value) {
      $('preview').disabled = !state.columns.length;
      $('filter-reset').disabled = !activeFilters().length;
      $('apply').disabled = !state.preview?.token || !!state.preview.unresolved || !state.preview.selected;
      $('preview-prev').disabled = !state.page;
      $('preview-next').disabled = !state.preview || (state.page + 1) * 30 >= state.preview.rows.length;
    }
  }
  async function api(action) {
    const form = new FormData();
    form.append('file', $('file').files[0]);
    form.append('filters', JSON.stringify(activeFilters()));
    form.append('decisions', JSON.stringify(state.decisions));
    form.append('token', state.preview?.token || '');
    const response = await fetch('/api/outstaff/' + action, {method: 'POST', body: form,
      headers: {'X-CSRF-Token': document.querySelector('.app-shell').dataset.csrf}, cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось выполнить импорт.');
    return data;
  }
  function renderFilters() {
    $('filter-list').replaceChildren(...state.conditions.map(condition => {
      const column = state.columns.find(c => c.index === condition.column);
      const label = column.name + ' · столбец ' + (column.index + 1);
      const operations = {'': 'Без фильтра', in: 'Равно одному из', not_in: 'Не равно', contains: 'Содержит', empty: 'Пусто', not_empty: 'Заполнено', before: 'Дата не позже', after: 'Дата не раньше'};
      const operation = el('select', {'aria-label': 'Фильтр: ' + label}, ...Object.entries(operations).map(([v, text]) => option(v, text)));
      operation.value = condition.op;
      const values = column.values || [];
      let input;
      if (['in', 'not_in'].includes(condition.op)) {
        const search = el('input', {type: 'search', placeholder: 'Найти значение', 'aria-label': 'Поиск значений: ' + label});
        const selected = el('small', {}, 'Выбрано значений: ' + condition.values.length);
        const choices = el('div', {className: 'outstaff-filter-values', role: 'group', 'aria-label': 'Значения: ' + label});
        const drawValues = () => {
          const query = search.value.toLocaleLowerCase('ru');
          const matches = values.filter(value => (value || '(пусто)').toLocaleLowerCase('ru').includes(query));
          choices.replaceChildren(...matches.map(value => {
            const check = el('input', {type: 'checkbox', checked: condition.values.includes(value)});
            check.onchange = () => {
              condition.values = check.checked ? [...condition.values, value] : condition.values.filter(item => item !== value);
              selected.textContent = 'Выбрано значений: ' + condition.values.length; filtersChanged();
            };
            return el('label', {}, check, el('span', {}, value || '(пусто)'));
          }));
          if (!matches.length) choices.append(el('small', {}, 'Значения не найдены.'));
        };
        search.oninput = drawValues; drawValues();
        input = el('div', {className: 'outstaff-filter-options'}, search, selected, choices);
      } else if (!condition.op) input = el('small', {}, 'Все значения');
      else if (['empty', 'not_empty'].includes(condition.op)) input = el('small', {}, 'Дополнительное значение не требуется');
      else {
        input = el('input', {type: ['before', 'after'].includes(condition.op) ? 'date' : 'text', value: condition.values[0] || '', 'aria-label': 'Значение фильтра: ' + label});
        input.oninput = () => { condition.values = input.value ? [input.value] : []; filtersChanged(); };
      }
      operation.onchange = () => { condition.op = operation.value; condition.values = []; filtersChanged(); renderFilters(); };
      return el('div', {className: 'outstaff-filter' + (condition.op ? ' active' : '')},
        el('label', {}, el('strong', {}, label), operation), input);
    }));
    filterInfo();
  }
  function renderMapping() {
    const data = state.preview;
    const sections = [];
    for (const [kind, title, sourceKey, targetKey, reasonKey] of [
      ['departments', 'СМУ → строительный участок', 'department', 'mapped_department', 'department_reason'],
      ['categories', 'ГДЛР ЛГСС / вид работ → категория ГДЛР', 'category_key', 'category_id', 'category_reason']]) {
      const groups = new Map();
      data.rows.forEach(row => {const key = row[sourceKey]; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(row);});
      const details = el('details', {open: true}, el('summary', {}, title));
      for (const [source, rows] of groups) {
        const targets = new Set(rows.map(row => row[targetKey]));
        const choices = kind === 'categories' ? data.categories.map(c => option(c.id, c.name)) : data.departments.map(d => option(d, d));
        const select = el('select', {'aria-label': title + ': ' + (source || '(пусто)')}, option('', 'Выберите соответствие'), ...choices);
        select.value = String(state.decisions[kind][source] ?? (targets.size === 1 ? rows[0][targetKey] || '' : ''));
        select.onchange = () => {
          if (select.value) state.decisions[kind][source] = kind === 'categories' ? Number(select.value) : select.value;
          else delete state.decisions[kind][source];
          invalidate(); $('import-status').textContent = 'Соответствия изменены. Нажмите «Проверить и сопоставить» ещё раз.';
        };
        details.append(el('div', {className: 'outstaff-map'}, el('div', {}, el('strong', {}, source || '(пусто)'), el('small', {}, rows.length + ' чел. · ' + [...new Set(rows.map(r => r[reasonKey]))].join('; '))), select));
      }
      sections.push(details);
    }
    $('mapping').replaceChildren(el('p', {}, 'Ручной выбор применяется к строкам с одинаковым исходным значением. Существующие индивидуальные привязки ГДЛР сохраняются; их можно изменить после импорта в списке.'), ...sections);
  }
  function renderRows() {
    const data = state.preview; if (!data) return;
    $('preview-list').replaceChildren(...data.rows.slice(state.page * 30, (state.page + 1) * 30).map(row => {
      const card = el('article', {className: 'outstaff-preview-row'}, el('strong', {}, row.full_name),
        el('p', {}, 'Строка ' + row.source_row + ' · ' + row.vendor + ' · ' + row.profession),
        el('p', {}, (row.mapped_department || 'СМУ не выбран') + ' · ' + (data.categories.find(c => c.id === row.category_id)?.name || 'ГДЛР не выбрана')),
        el('small', {}, row.category_reason + ' · ' + (row.worker_id ? 'Существующий сотрудник' : 'Новый сотрудник с внутренним кодом OUT')));
      if (row.needs_identity) {
        const select = el('select', {'aria-label': 'Совпадение ФИО: строка ' + row.source_row}, option('', 'Уточните совпадение ФИО'), option('new', 'Это другой человек — создать отдельно'), ...row.identity_candidates.map(c => option(c.id, c.number + ' · ' + c.vendor + ' · ' + c.name)));
        select.value = String(state.decisions.identities[row.source_row] || '');
        select.onchange = () => {state.decisions.identities[row.source_row] = select.value === 'new' ? 'new' : Number(select.value); invalidate();};
        card.append(select);
      }
      if (row.errors.length) card.append(el('p', {className: 'error-text'}, row.errors.join(' ')));
      return card;
    }));
    $('preview-page').textContent = 'Страница ' + (state.page + 1) + ' из ' + Math.max(1, Math.ceil(data.rows.length / 30));
    busy(false);
  }
  $('import-open').onclick = () => $('import-dialog').showModal();
  $('import-close').onclick = () => {if (!state.busy) $('import-dialog').close();};
  $('import-dialog').addEventListener('cancel', e => {if (state.busy) e.preventDefault();});
  $('file').onchange = async () => {
    state.preview = null; state.columns = []; state.conditions = []; state.decisions = {categories: {}, departments: {}, identities: {}};
    $('mapping').replaceChildren(); $('preview-list').replaceChildren(); $('preview-page').textContent = ''; $('file-info').textContent = ''; renderFilters(); $('import-error').textContent = ''; $('import-status').textContent = '';
    if (!$('file').files.length) {busy(false); return;}
    busy(true); $('file-info').textContent = 'Чтение структуры файла…';
    try {
      const data = await api('inspect'); state.columns = data.columns;
      state.conditions = data.columns.map(column => ({column: column.index, op: '', values: []}));
      renderFilters();
      $('file-info').textContent = 'Лист «' + data.sheet + '»: ' + data.total + ' строк, столбцов: ' + data.columns.length + '.';
    }
    catch (failure) { $('import-error').textContent = failure.message; $('file-info').textContent = ''; }
    finally { busy(false); }
  };
  $('filter-reset').onclick = () => {state.conditions.forEach(condition => {condition.op = ''; condition.values = [];}); filtersChanged(); renderFilters();};
  $('preview').onclick = async () => {
    busy(true); invalidate(); $('import-error').textContent = ''; $('import-status').textContent = 'Проверка и сопоставление…';
    try {state.preview = await api('preview'); state.page = 0; renderMapping(); renderRows(); $('import-status').textContent = 'Выбрано ' + state.preview.selected + ' из ' + state.preview.total + '. Требуют уточнения: ' + state.preview.unresolved + '.';}
    catch (failure) {$('import-error').textContent = failure.message; $('import-status').textContent = '';}
    finally {busy(false);}
  };
  $('apply').onclick = async () => {
    busy(true); $('import-error').textContent = ''; $('import-status').textContent = 'Импорт сотрудников…';
    try {const data = await api('apply'); invalidate(); $('import-status').textContent = (data.already_imported ? 'Этот набор уже импортирован: ' : 'Импорт завершён: ') + data.selected + ' сотрудников. Они доступны во вкладках «Аутстафф» и «Расстановка».'; await window.outstaffScreen.load();}
    catch (failure) {$('import-error').textContent = failure.message;}
    finally {busy(false);}
  };
  $('preview-prev').onclick = () => {state.page--; renderRows();};
  $('preview-next').onclick = () => {state.page++; renderRows();};
  window.outstaffImport = {canLeave: () => !state.busy};
})();
