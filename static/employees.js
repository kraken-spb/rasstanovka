function createEmployeeScreen(prefix) {
  'use strict';
  const MF = window.MultiFilter;
  const isOutstaff = prefix === 'outstaff';
  const $ = id => document.getElementById(id.replace('employees', prefix));
  if (!$('view-employees')) return;
  const root = document.querySelector('.app-shell');
  const readOnly = root.dataset.role === 'hr_viewer';
  const state = {rows: [], crews: [], categories: [], categoryDrafts: new Map(), filtered: [], page: 0, busy: false, drafts: new Map(), request: 0, worker: null, summary: null, reportDate: $('employees-date').value};
  ['employees-crew-filter','employees-category','employees-active'].forEach(id => MF.enable($(id)));
  let size = 50;
  const pager = window.TablePagination.mount($('employees-list'), isOutstaff ? 'Аутстафф' : 'Сотрудники', (page, count) => {
    if (!canLeave()) return false;
    state.page = page; size = count; render();
    $('employees-list').scrollTop = 0;
  });
  const fields = ['full_name', 'personnel_no', 'crew_name', 'category', 'profession', 'gsp_profession', 'pps',
    'department', 'employer', 'contractor', 'owner_name', 'linear_itr_name', 'brigadier_name', 'outstaff_search'];
  const normalize = value => String(value || '').toLocaleLowerCase('ru').replace(/ё/g, 'е');
  const el = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    Object.entries(props).forEach(([key, value]) => {
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key in node) node[key] = value;
      else node.setAttribute(key, value);
    });
    children.flat().forEach(child => { if (child != null) node.append(child); });
    return node;
  };
  const option = (value, label) => el('option', {value: String(value)}, label);
  function status(message) { $('employees-status').textContent = message; }
  function error(message = '') { $('employees-error').textContent = message; $('employees-error').hidden = !message; }
  async function api(url, options = {}) {
    const response = await fetch(url, {...options, cache: 'no-store', headers: {
      'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось выполнить запрос.');
    return data;
  }
  function canLeave() {
    if (state.restoring) { status('Завершите восстановление сотрудника или нажмите «Отмена».'); return false; }
    if (state.creating) { status('Завершите добавление сотрудника или нажмите «Отмена».'); return false; }
    if (state.busy || state.drafts.size || state.categoryDrafts.size) {
      status(state.busy ? 'Дождитесь сохранения.' : 'Сохраните или отмените изменения в строках сотрудников.');
      return false;
    }
    return true;
  }
  async function load() {
    if (!canLeave()) return;
    state.request++; state.worker?.terminate(); state.worker = null;
    state.summary = null; renderStats();
    state.busy = true; $('view-employees').inert = true; status('Загрузка сотрудников…');
    try {
      const [employees, crews, categories] = await Promise.all([api(employeesUrl()), api('/api/crews'), api('/api/gdlr-categories')]);
      acceptEmployees(employees); state.crews = crews.rows; state.categories = categories.rows; state.needsRefresh = false;
      updateFilters();
      status(''); filter();
    } catch (failure) { state.rows = []; state.filtered = []; render(); error(failure.message); }
    finally { state.busy = false; $('view-employees').inert = false; }
  }
  function employeesUrl() { return '/api/employees?date=' + encodeURIComponent($('employees-date').value) + (isOutstaff ? '&scope=outstaff' : ''); }
  function acceptEmployees(data) { state.rows = data.rows.map(row => ({...row, source_category: isOutstaff ? row.outstaff.source_category : row.source_category, outstaff_search: row.outstaff ? Object.values(row.outstaff).join(' ') : ''})); state.summary = data.summary; state.reportDate = data.date; state.departments = data.departments || []; if (isOutstaff) state.summary.workers = state.rows.filter(row => row.category_id).length; }
  function renderStats() {
    const filtered = {total: state.filtered.length, workers: state.filtered.filter(row => isOutstaff ? row.category_id : row.is_worker).length,
      assigned: state.filtered.filter(row => row.assigned_on_date).length};
    for (const key of ['total', 'workers', 'assigned']) {
      $('employees-count-' + key).textContent = state.summary ? state.summary[key].toLocaleString('ru-RU') : '—';
      $('employees-filtered-' + key).textContent = state.summary && state.filtered.length !== state.rows.length
        ? 'По фильтрам: ' + filtered[key].toLocaleString('ru-RU') : '';
    }
    $('employees-stats-note').textContent = 'Состав сотрудников — текущий. Расставленные учитываются один раз за обе смены отчётной даты. Рабочие — по квалификации из файла или ручного добавления.'
      + (state.summary?.unknown_qualification ? ' Квалификация не указана или неоднозначна: ' + state.summary.unknown_qualification + '.' : '');
    if (isOutstaff) $('employees-stats-note').textContent = 'Состав аутстаффа — все импортированные сотрудники. Расставленные учитываются один раз за обе смены отчётной даты. Код OUT — внутренний идентификатор, в файле табельного номера нет.';
  }
  function updateFilters() {
      const selected = MF.get($('employees-crew-filter'));
      const names = new Map(state.rows.filter(row => row.crew_id).map(row => [row.crew_id, row.crew_name]));
      $('employees-crew-filter').replaceChildren(option('', 'Все бригады'), option('none', 'Без бригады'),
        ...[...names].sort((a, b) => a[1].localeCompare(b[1], 'ru', {numeric: true})).map(([id, name]) => option(id, name)));
      MF.set($('employees-crew-filter'), selected);
      const category = MF.get($('employees-category'));
      $('employees-category').replaceChildren(option('', 'Все категории'), option('none', 'Не привязана к справочнику'),
        ...state.categories.map(item => option(item.id, item.name + (item.active ? '' : ' · отключена'))));
      MF.set($('employees-category'), category);
  }
  function filter(resetPage = true) {
    const previousPage = resetPage ? 0 : state.page;
    const request = ++state.request;
    state.worker?.terminate(); state.worker = null;
    error(); status(''); state.page = previousPage;
    const crew = MF.get($('employees-crew-filter')), category = MF.get($('employees-category')), active = MF.get($('employees-active'));
    const rows = state.rows.filter(row => MF.matches(crew, row.crew_id || 'none')
      && MF.matches(category, row.category_id === null ? 'none' : row.category_id) && MF.matches(active, row.active));
    const query = $('employees-search').value.trim();
    if (!$('employees-regex').checked || !query) {
      const words = normalize(query).split(/\s+/).filter(Boolean);
      state.filtered = rows.filter(row => { const text = normalize(fields.map(key => row[key]).join(' ')); return words.every(word => text.includes(word)); });
      render(); return;
    }
    state.filtered = []; render(); status('Поиск Regex…');
    const worker = new Worker('/static/employee-search.js'); state.worker = worker;
    const finish = (message, ids = []) => {
      clearTimeout(timer); worker.terminate();
      if (request !== state.request) return;
      state.worker = null; status(''); error(message);
      const matches = new Set(ids); state.filtered = rows.filter(row => matches.has(row.id)); state.page = previousPage; render();
    };
    const timer = setTimeout(() => finish('Regex выполняется слишком долго. Упростите выражение.'), 1000);
    worker.onmessage = ({data}) => finish(data.error || '', data.ids);
    worker.onerror = () => finish('Не удалось выполнить Regex-поиск. Повторите запрос.');
    worker.postMessage({query: SearchRegex.source(query), rows: rows.map(row => ({id: row.id, fields: fields.map(key => String(row[key] || ''))}))});
  }
  function cell(label, content) { return el('td', {'data-label': label}, content || '—'); }
  function details(...values) { return el('div', {}, ...values.map(([label, value]) => el('div', {className: 'employee-detail'}, el('small', {}, label), el('span', {}, value || '—')))); }
  function categoryBinding(row) {
    const binding = el('div', {className: 'employee-binding'}, el('strong', {}, row.category || '—'),
      el('small', {}, (row.manual_registration ? 'Исходная категория: ' : 'Из файла: ') + (row.source_category || '—')));
    if (!row.category_id) binding.append(el('small', {}, 'Не привязана к справочнику'));
    else if (!row.category_active) binding.append(el('small', {}, 'Категория отключена'));
    if (readOnly || !row.can_edit) return binding;
    const select = el('select', {'aria-label': 'Категория ГДЛР: ' + row.full_name}, option('', 'Выберите категорию'),
      ...state.categories.filter(item => item.active || item.id === row.category_id).map(item => {
        const node = option(item.id, item.name + (item.active ? '' : ' · отключена')); node.disabled = !item.active; return node;
      }));
    select.value = String(state.categoryDrafts.get(row.id)?.category_id || row.category_id || '');
    const save = el('button', {className: 'primary-button', disabled: !state.categoryDrafts.has(row.id), onclick: () => saveCategory(row)}, 'Сохранить категорию');
    const cancel = el('button', {className: 'text-button', hidden: !state.categoryDrafts.has(row.id), onclick: () => {state.categoryDrafts.delete(row.id); render(); status('Изменение категории отменено.');}}, 'Отмена');
    select.addEventListener('change', () => {
      const category = state.categories.find(item => String(item.id) === select.value);
      if (!category || category.id === row.category_id) state.categoryDrafts.delete(row.id);
      else state.categoryDrafts.set(row.id, {category_id: category.id, category_token: category.edit_token, expected_token: row.membership_token});
      save.disabled = !state.categoryDrafts.has(row.id); cancel.hidden = save.disabled;
    });
    binding.append(select, el('div', {className: 'employee-actions'}, save, cancel));
    if (!state.categories.some(item => item.active)) binding.append(el('small', {}, 'Супер-администратор должен добавить категории в справочник.'));
    return binding;
  }
  async function saveCategory(row) {
    if (state.busy || !state.categoryDrafts.has(row.id)) return;
    state.busy = true; $('view-employees').inert = true; error(); status('Сохранение категории: ' + row.full_name);
    try {
      await api('/api/employees/' + row.id + '/category', {method: 'PUT', body: JSON.stringify(state.categoryDrafts.get(row.id))});
      state.categoryDrafts.delete(row.id);
      const [employees, categories] = await Promise.all([api(employeesUrl()), api('/api/gdlr-categories')]);
      acceptEmployees(employees); state.categories = categories.rows;
      updateFilters(); filter(); status('Категория сохранена: ' + row.full_name);
    } catch (failure) { error(failure.message + ' Отмените выбор и обновите список для проверки сохранения.'); }
    finally { state.busy = false; $('view-employees').inert = false; }
  }
  const employeeColumns = [
    ['full_name', 'ФИО сотрудника', 12], ['personnel_no', 'Табельный №', 6], ['pps', 'ППС', 12],
    ['crew_name', 'Бригада', 10], ['category', 'Категория ГДЛР', 8],
    ['source_category', 'Категория из источника', 8, true], ['profession', 'Должность', 11],
    ['gsp_profession', 'Профессия ГСП', 9, true], ['department', 'Участок', 10],
    ['employer', 'Организация-работодатель', 7], ['contractor', 'Компания-подрядчик', 6],
    ['linear_itr_name', 'Линейный ИТР', 8], ['brigadier_name', 'Бригадир', 8],
    ['active', 'Статус', 6], ['removal_date', 'Дата удаления', 6, true],
    ['removal_reason', 'Причина удаления', 10, true], ['actions', 'Действия', 6]
  ];
  if (isOutstaff) employeeColumns.splice(0, employeeColumns.length,
    ['full_name', 'ФИО сотрудника', 15], ['personnel_no', 'Код сотрудника', 8],
    ['crew_name', 'Бригада', 14], ['category', 'Категория ГДЛР', 12],
    ['source_category', 'ГДЛР ЛГСС из файла', 10, true], ['profession', 'Должность', 11],
    ['department', 'Участок', 13], ['source_department', 'СМУ из файла', 9, true],
    ['vendor', 'Контрагент', 10], ['work_kind', 'Вид работ', 9], ['staff_type', 'Вид', 9],
    ['pps', 'ППС', 8, true], ['gsp_profession', 'Профессия ГСП', 9, true],
    ['employer', 'Организация-работодатель', 10, true], ['contractor', 'Компания-подрядчик', 8, true],
    ['linear_itr_name', 'Линейный ИТР', 10, true], ['brigadier_name', 'Бригадир', 10, true],
    ['active', 'Статус', 7, true], ['removal_date', 'Дата удаления', 8, true],
    ['removal_reason', 'Причина удаления', 10, true], ['actions', 'Действия', 7]);
  const leadingColumns = ['employer', 'contractor'];
  leadingColumns.slice().reverse().forEach(key => {
    const index = employeeColumns.findIndex(column => column[0] === key);
    const [column] = employeeColumns.splice(index, 1);
    column[3] = false;
    employeeColumns.unshift(column);
  });
  const columnsKey = prefix + '-grid-columns-v2';
  const defaultColumns = employeeColumns.filter(column => !column[3]).map(column => column[0]);
  let visibleColumns = new Set(defaultColumns);
  {
    try {
      let saved = JSON.parse(localStorage.getItem(columnsKey));
      if (!Array.isArray(saved)) {
        const previous = JSON.parse(localStorage.getItem(prefix + '-grid-columns-v1'));
        if (Array.isArray(previous)) {
          saved = [...new Set([...leadingColumns, ...previous])];
          localStorage.setItem(columnsKey, JSON.stringify(saved));
        }
      }
      if (Array.isArray(saved)) visibleColumns = new Set(['full_name', ...saved.filter(key => employeeColumns.some(column => column[0] === key))]);
    } catch (_) { /* Column preferences are optional when browser storage is unavailable. */ }
    const checks = el('div', {className: 'employee-columns-options'});
    const menu = el('details', {className: 'employee-columns-menu'}, el('summary', {}, 'Столбцы'), checks);
    const paintChecks = () => checks.replaceChildren(...employeeColumns.map(([key, label]) => {
      const check = el('input', {type: 'checkbox', checked: visibleColumns.has(key), disabled: key === 'full_name'});
      check.addEventListener('change', () => {
        if (!canLeave()) { check.checked = visibleColumns.has(key); return; }
        check.checked ? visibleColumns.add(key) : visibleColumns.delete(key);
        try { localStorage.setItem(columnsKey, JSON.stringify([...visibleColumns])); } catch (_) { /* Keep preferences for this session. */ }
        render();
      });
      return el('label', {className: 'check-label'}, check, label);
    }), el('button', {type: 'button', className: 'text-button', onclick: () => {
      if (!canLeave()) return;
      visibleColumns = new Set(defaultColumns);
      try { localStorage.setItem(columnsKey, JSON.stringify(defaultColumns)); } catch (_) { /* Keep preferences for this session. */ }
      paintChecks(); render();
    }}, 'Вернуть исходный вид'));
    paintChecks();
    $('employees-search').closest('.employees-toolbar').append(menu);
  }
  function gridColumns() { return employeeColumns.filter(column => visibleColumns.has(column[0])); }
  function employeeSelect(row, kind) {
    if (readOnly || !row.can_edit) return row[kind === 'crew' ? 'crew_name' : 'category'] || (kind === 'crew' ? 'Без бригады' : '—');
    const options = kind === 'crew' ? [option('', 'Без бригады'), ...state.crews.map(crew => option(crew.id, crew.name))]
      : [el('option', {value: '', disabled: true}, 'Выберите категорию'), ...state.categories.filter(c => c.active || c.id === row.category_id).map(c =>
        el('option', {value: String(c.id), disabled: !c.active}, c.name + (c.active ? '' : ' · отключена')))];
    const select = el('select', {disabled: !!state.needsRefresh, 'aria-label': (kind === 'crew' ? 'Бригада: ' : 'Категория ГДЛР: ') + row.full_name,
      title: row[kind === 'crew' ? 'crew_name' : 'category'] || ''}, ...options);
    select.value = String((kind === 'crew' ? row.crew_id : row.category_id) || '');
    select.addEventListener('change', () => {
      const target = select.value ? Number(select.value) : null;
      if (target === (kind === 'crew' ? row.crew_id : row.category_id)) return;
      const category = kind === 'category' ? state.categories.find(item => item.id === target && item.active) : null;
      if (kind === 'category' && !category) { select.value = String(row.category_id || ''); return; }
      saveEmployeeChoice(row, kind, kind === 'crew' ? {crew_id: target, expected_token: row.membership_token}
        : {category_id: target, category_token: category.edit_token, expected_token: row.membership_token});
    });
    return select;
  }
  function employeePpsSelect(row) {
    if (readOnly || !row.can_edit) return row.pps || 'Не указана';
    const select = el('select', {disabled: !!state.needsRefresh, 'aria-label': 'ППС: ' + row.full_name},
      option('', 'Не указана'), option('ППС15', 'ППС15'), option('ППС19', 'ППС19'));
    select.value = row.pps || '';
    select.addEventListener('change', () => {
      if (select.value === (row.pps || '')) return;
      saveEmployeeChoice(row, 'pps', {pps: select.value, expected_token: row.membership_token});
    });
    return select;
  }
  function outstaffDepartmentSelect(row) {
    if (!['admin', 'super_admin'].includes(root.dataset.role)) return row.department || '—';
    const select = el('select', {disabled: !!state.needsRefresh, 'aria-label': 'Участок: ' + row.full_name,
      title: row.department || ''}, el('option', {value: '', disabled: true}, 'Выберите участок'),
      ...state.departments.map(department => option(department, department)));
    select.value = row.department || '';
    select.addEventListener('change', () => {
      if (!select.value || select.value === row.department) return;
      saveEmployeeChoice(row, 'department', {department: select.value, expected_token: row.outstaff.edit_token});
    });
    return select;
  }
  function renderEmployeeRow(row) {
    const values = {...row, pps: employeePpsSelect(row), crew_name: employeeSelect(row, 'crew'), category: employeeSelect(row, 'category'),
      full_name: el('strong', {}, row.full_name), active: row.active ? 'Действующий' : 'Отключён',
      removal_date: row.removal?.effective_date?.split('-').reverse().join('.'), removal_reason: row.removal?.reason,
      actions: !readOnly && row.can_remove ? el('button', {type: 'button', className: 'text-button employee-grid-remove',
        disabled: !!state.needsRefresh, 'aria-label': 'Удалить сотрудника: ' + row.full_name, onclick: () => openRemoval(row)}, 'Удалить')
        : !readOnly && row.can_restore ? restoreButton(row) : '—'};
    if (isOutstaff) {
      Object.assign(values, {department: outstaffDepartmentSelect(row), source_department: row.outstaff.source_department,
        vendor: row.outstaff.vendor, work_kind: row.outstaff.work_kind, staff_type: row.outstaff.staff_type});
      if (!row.category_id) values.category = el('div', {className: 'employee-binding'}, values.category,
        el('small', {}, (readOnly ? 'В файле: ' : 'Выберите вручную · в файле: ') + (row.outstaff.source_category || 'не указана')));
    }
    return el('tr', {'data-employee-id': row.id}, ...gridColumns().map(([key, label]) =>
      el('td', {'data-label': label, 'data-column': key, title: typeof values[key] === 'string' ? values[key] : ''}, values[key] || '—')));
  }
  async function saveEmployeeChoice(row, kind, payload) {
    if (state.busy || state.needsRefresh || !canLeave()) return;
    const viewport = $('employees-list'), position = {top: viewport.scrollTop, left: viewport.scrollLeft};
    state.busy = true; $('view-employees').inert = true; error(); status('Сохранение: ' + row.full_name);
    let failureMessage = '';
    try { await api((kind === 'department' ? '/api/outstaff/' : '/api/employees/') + row.id + '/' + kind, {method: 'PUT', body: JSON.stringify(payload)}); }
    catch (failure) { failureMessage = failure.message; }
    try {
      const [employees, crews, categories] = await Promise.all([api(employeesUrl()), api('/api/crews'), api('/api/gdlr-categories')]);
      acceptEmployees(employees); state.crews = crews.rows; state.categories = categories.rows; state.needsRefresh = false;
      updateFilters(); filter(false);
      viewport.scrollTop = position.top; viewport.scrollLeft = position.left;
      if (failureMessage) { error(failureMessage); status('Показаны актуальные данные с сервера.'); }
      else status(({crew: 'Бригада сохранена: ', category: 'Категория сохранена: ', pps: 'ППС сохранена: ', department: 'Участок сохранён: '}[kind]) + row.full_name);
    } catch (failure) {
      state.needsRefresh = true; render();
      status('Проверка сохранения не завершена.');
      error((failureMessage ? failureMessage + ' ' : '') + 'Не удалось перечитать данные. Нажмите «Обновить» перед следующим изменением.');
    } finally { state.busy = false; $('view-employees').inert = false; }
  }

  function render() {
    state.page = Math.min(state.page, Math.max(0, Math.ceil(state.filtered.length / size) - 1));
    renderStats();
    const columns = gridColumns();
    const body = el('tbody', {}, ...state.filtered.slice(state.page * size, (state.page + 1) * size).map(renderEmployeeRow));
    const totalWidth = columns.reduce((sum, column) => sum + column[2], 0);
    $('employees-list').replaceChildren(state.filtered.length ? el('table', {className: 'employees-table employees-grid'},
      el('colgroup', {}, ...columns.map(column => el('col', {style: 'width:' + column[2] / totalWidth * 100 + '%'}))),
      el('thead', {}, el('tr', {}, ...columns.map(column => el('th', {scope: 'col'}, column[1])))), body)
      : el('div', {className: 'empty-state'}, 'Сотрудники не найдены.'));
    pager.update(state.filtered.length, state.page, size);
  }
  async function saveCrew(row) {
    if (state.busy || !state.drafts.has(row.id)) return;
    state.busy = true; $('view-employees').inert = true; error(); status('Сохранение: ' + row.full_name);
    try {
      await api('/api/employees/' + row.id + '/crew', {method: 'PUT', body: JSON.stringify({crew_id: state.drafts.get(row.id), expected_token: row.membership_token})});
      const data = await api(employeesUrl()); acceptEmployees(data);
      state.drafts.delete(row.id); updateFilters(); filter(); status('Сохранено: ' + row.full_name);
    } catch (failure) { error(failure.message + ' Если связь прервалась, отмените выбор и обновите список для проверки сохранения.'); }
    finally { state.busy = false; $('view-employees').inert = false; }
  }
  function restoreButton(row) {
    return el('button', {type: 'button', className: 'secondary-button', disabled: !!state.needsRefresh,
      'aria-label': 'Восстановить сотрудника: ' + row.full_name, onclick: () => openRestoration(row)}, 'Восстановить');
  }
  function openRestoration(row) {
    if (!canLeave() || state.needsRefresh) return;
    state.restoring = true;
    const dialog = el('dialog', {className: 'app-dialog employee-removal-dialog', 'aria-label': 'Восстановление сотрудника'});
    const failure = el('p', {className: 'error-text', role: 'alert'});
    const save = el('button', {type: 'submit', className: 'primary-button'}, 'Восстановить сотрудника');
    const cancel = el('button', {type: 'button', className: 'secondary-button', onclick: () => dialog.close()}, 'Отмена');
    const form = el('form', {}, el('h2', {}, 'Восстановление сотрудника'),
      el('p', {}, el('strong', {}, row.full_name), el('br', {}), (isOutstaff ? 'Код ' : 'Таб. № ') + row.personnel_no),
      el('p', {className: 'table-note'}, 'Сотрудник вернётся в действующий состав и станет доступен для расстановки. Ранее снятые назначения не восстановятся автоматически. История удаления сохранится в журнале.'),
      failure, el('div', {className: 'employee-actions'}, save, cancel));
    let saving = false;
    dialog.addEventListener('cancel', event => { if (saving) event.preventDefault(); });
    dialog.addEventListener('close', () => { state.restoring = false; dialog.remove(); });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (saving || save.disabled) return;
      saving = true; state.busy = true; form.inert = true;
      try {
        await api('/api/employees/' + row.id + '/restore', {method: 'POST', body: JSON.stringify({expected_token: row.restoration_token})});
        state.restoring = false; dialog.close(); state.busy = false; await load();
        status('Сотрудник восстановлен: ' + row.full_name + '. Он доступен в фильтре «Действующие».');
      } catch (problem) {
        failure.textContent = problem.message + ' Закройте окно и обновите список перед повторной попыткой.';
        save.disabled = true; state.needsRefresh = true;
      } finally { saving = false; state.busy = false; form.inert = false; }
    });
    dialog.append(form); document.body.append(dialog); dialog.showModal(); cancel.focus();
  }
  function openRemoval(row) {
    if (!canLeave()) return;
    const today = new Intl.DateTimeFormat('en-CA', {timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
    const dialog = el('dialog', {className: 'app-dialog employee-removal-dialog', 'aria-label': 'Удаление сотрудника'});
    const day = el('input', {type: 'date', required: true, value: today, max: today});
    const reason = el('textarea', {required: true, maxLength: 1000, rows: 3, placeholder: 'Например: увольнение по собственному желанию'});
    const preview = el('p', {className: 'table-note'}), failure = el('p', {className: 'error-text', role: 'alert'});
    const save = el('button', {type: 'submit', className: 'primary-button employee-remove-submit', disabled: true}, 'Удалить сотрудника');
    const cancel = el('button', {type: 'button', className: 'secondary-button', onclick: () => dialog.close()}, 'Отмена');
    const form = el('form', {}, el('h2', {}, 'Удаление сотрудника'),
      el('p', {}, el('strong', {}, row.full_name), el('br', {}), 'Таб. № ' + row.personnel_no),
      el('label', {}, 'Дата увольнения', day), el('label', {}, 'Причина удаления', reason), preview, failure,
      el('div', {className: 'employee-actions'}, save, cancel));
    dialog.append(form); document.body.append(dialog); dialog.showModal();
    let snapshot = null, request = 0, saving = false;
    const refresh = async () => {
      const current = ++request; snapshot = null; save.disabled = true; failure.textContent = '';
      preview.textContent = 'Проверка назначений…';
      if (!day.validity.valid) { preview.textContent = 'Укажите дату увольнения не позже сегодняшней.'; return; }
      try {
        const data = await api('/api/employees/' + row.id + '/removal-preview?date=' + encodeURIComponent(day.value));
        if (current !== request || !dialog.open) return;
        if (data.full_name !== row.full_name || data.personnel_no !== row.personnel_no) throw new Error('Данные сотрудника изменились. Обновите список.');
        snapshot = data;
        preview.textContent = 'Сотрудник будет удалён из действующего состава. Назначений с этой даты будет снято: ' + data.assignment_count +
          '. История до этой даты сохранится. Причина и автор удаления будут записаны в журнал.';
        save.disabled = false;
      } catch (error) { if (current === request && dialog.open) { preview.textContent = ''; failure.textContent = error.message; } }
    };
    day.addEventListener('change', refresh);
    dialog.addEventListener('cancel', event => { if (saving) event.preventDefault(); });
    dialog.addEventListener('close', () => { request++; dialog.remove(); });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!snapshot || snapshot.date !== day.value || saving) return;
      if (!reason.value.trim()) { reason.setCustomValidity('Укажите причину удаления.'); reason.reportValidity(); return; }
      saving = true; state.busy = true; form.inert = true;
      try {
        await api('/api/employees/' + row.id, {method: 'DELETE', body: JSON.stringify({date: snapshot.date,
          reason: reason.value.trim(), expected_token: snapshot.expected_token})});
        dialog.close(); state.busy = false; await load(); status('Сотрудник удалён. Причина сохранена в журнале: ' + row.full_name);
      } catch (error) {
        failure.textContent = error.message + ' Закройте окно и обновите список перед повторной попыткой.';
        save.disabled = true; snapshot = null;
      } finally { saving = false; state.busy = false; form.inert = false; }
    });
    reason.addEventListener('input', () => reason.setCustomValidity(''));
    refresh(); reason.focus();
  }
  for (const id of ['employees-search', 'employees-regex', 'employees-crew-filter', 'employees-category', 'employees-active']) {
    const node = $(id); let previous = node.type === 'checkbox' ? node.checked : node.value;
    node.addEventListener(id === 'employees-search' ? 'input' : 'change', () => {
      if (!canLeave()) { if (node.type === 'checkbox') node.checked = previous; else node.value = previous; return; }
      previous = node.type === 'checkbox' ? node.checked : node.value; filter();
    });
  }
  if (!isOutstaff && $('employees-create')) $('employees-create').addEventListener('click', () => {
    if (!canLeave()) return;
    state.creating = true;
    window.employeeCreator.open({onClosed: () => { state.creating = false; }, onSaved: async result => {
      await load();
      MF.set($('employees-crew-filter'), selected); MF.set($('employees-category'), category); MF.set($('employees-active'), '1');
      $('employees-regex').checked = false; $('employees-search').value = result.personnel_no;
      filter();
      status('Сотрудник добавлен: ' + result.full_name + ' · ' + result.personnel_no + '.');
    }});
  });
  $('employees-refresh').onclick = load;
  $('employees-date').addEventListener('change', () => {
    if (!canLeave() || !$('employees-date').value || !$('employees-date').validity.valid) {
      $('employees-date').value = state.reportDate; return;
    }
    load();
  });
  window.addEventListener('beforeunload', event => { if (state.busy || state.drafts.size || state.categoryDrafts.size) {event.preventDefault(); event.returnValue = '';} });
  window[isOutstaff ? 'outstaffScreen' : 'employeesScreen'] = {load, canLeave};
}
createEmployeeScreen('employees');
createEmployeeScreen('outstaff');
