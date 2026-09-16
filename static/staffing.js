(() => {
  "use strict";
  const MF = window.MultiFilter;
  const hierarchy = window.StaffingHierarchy;
  const $ = (id) => document.getElementById(id);
  if (!$('view-staffing')) return;
  const root = document.querySelector('.app-shell');
  const readOnly = ['hr_viewer', 'rotation', 'recruitment'].includes(root.dataset.role);
  const state = { rows: [], crews: [], crewOptions: [], collapsed: new Set(), selected: new Map(), drafts: new Map(), crewDrafts: new Map(),
    nodes: new Map(), crewNodes: new Map(), loadedCrews: new Set(), loadingCrews: new Map(), busy: false, request: 0,
    details: null, reference: null, imported: null, preview: null, search: '', regex: null, regexMode: false, department: '', employer: '', contractor: '', author: '', category: '', unassigned: false,
    date: $('staffing-date').value, shift: MF.get($('staffing-shift')), freshness: '', calendarFilter: null, groupMode: 'hierarchy', itrGroups: [], rowsByItr: new Map() };
  ["staffing-shift", "staffing-category", "staffing-department", "staffing-author", "staffing-freshness"].forEach(id => MF.enable($(id), ''));
  const preferences = window.staffingPreferences;
  state.pps = preferences.get('pps', '');
  state.date = preferences.get('date', state.date);
  $('staffing-date').value = state.date;
  for (const field of ['groupMode', 'department', 'employer', 'contractor', 'author', 'category', 'unassigned', 'search', 'regexMode', 'shift', 'freshness']) {
    state[field] = preferences.get(field, state[field]);
  }
  if (state.shift === 'all') state.shift = '';
  state.groupLevels = preferences.get('groupLevels', hierarchy.defaults);
  if (!hierarchy.valid(state.groupLevels)) state.groupLevels = [...hierarchy.defaults];
  updateHierarchyLabel();
  $('staffing-grouping').value = state.groupMode;
  MF.set($('staffing-shift'), state.shift);
  MF.set($('staffing-freshness'), state.freshness);
  $('staffing-unassigned').checked = state.unassigned;
  $('staffing-search').value = state.search;
  $('staffing-regex').checked = state.regexMode;
  try { state.regex = state.regexMode && state.search ? new RegExp(SearchRegex.source(state.search), 'iu') : null; }
  catch (_) { state.search = ''; state.regexMode = false; $('staffing-search').value = ''; $('staffing-regex').checked = false; }
  state.page = 0; state.pageSize = 50;
  const pager = window.TablePagination.mount($('staffing-table'), 'Расстановка', (page, size) => {
    if (!canLeave()) return false;
    state.page = page; state.pageSize = size; render();
    $('staffing-table').scrollTop = 0;
  });
  const headers = ['№п/п', 'Группа подобъектов', 'Подобъект', 'Компания подрядчик',
    'Организация-работодатель', 'ФИО работника', 'Таб. № с префиксом',
    'Категория ГДЛР', 'ФИО линейного ИТР', 'ФИО бригадира', 'Смена', 'Статус', 'Выполняемые работы', 'Кто назначил'];
  const el = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key in node) node[key] = value;
      else node.setAttribute(key, value);
    }
    children.flat().forEach(child => { if (child != null) node.append(child); });
    return node;
  };
  const employerFilter = el('select', {id: 'staffing-employer'});
  $('staffing-department').closest('label').after(el('label', {}, 'Организация-работодатель', employerFilter));
  MF.enable(employerFilter);
  employerFilter.addEventListener('change', () => {
    if (!canLeave()) { MF.set(employerFilter, state.employer); return; }
    state.employer = MF.get(employerFilter);
    if (!state.calendarFilter) preferences.set({employer: state.employer});
    render();
  });
  const contractorFilter = el('select', {id: 'staffing-contractor'});
  $('staffing-department').closest('label').after(el('label', {}, 'Компания-подрядчик', contractorFilter));
  MF.enable(contractorFilter);
  contractorFilter.addEventListener('change', () => {
    if (!canLeave()) { MF.set(contractorFilter, state.contractor); return; }
    state.contractor = MF.get(contractorFilter);
    if (!state.calendarFilter) preferences.set({contractor: state.contractor});
    render();
  });
  const ppsOptions = () => [el('option', {value: ''}, 'Все ППС'),
    el('option', {value: 'ППС15'}, 'ППС15'), el('option', {value: 'ППС19'}, 'ППС19')];
  const ppsFilter = el('select', {id: 'staffing-pps'}, ...ppsOptions());
  $('staffing-department').closest('label').before(el('label', {}, 'ППС', ppsFilter));
  MF.enable(ppsFilter); MF.set(ppsFilter, state.pps);
  ppsFilter.addEventListener('change', () => {
    if (!canLeave()) { MF.enable(ppsFilter); MF.set(ppsFilter, state.pps); return; }
    state.pps = MF.get(ppsFilter);
    if (!state.calendarFilter) preferences.set({pps: state.pps});
    render();
  });
  const importPps = el('select', {id: 'staffing-import-pps'},
    el('option', {value: ''}, 'Без отметки'), el('option', {value: 'ППС15'}, 'ППС15'), el('option', {value: 'ППС19'}, 'ППС19'));
  $('staffing-file')?.closest('label').before(el('label', {}, 'ППС загружаемого файла', importPps));
  importPps.addEventListener('change', () => {
    state.preview = null; $('staffing-import-apply').hidden = true;
  });
  const norm = (value) => String(value || '').toLocaleLowerCase('ru').replace(/ё/g, 'е');
  const matches = (value, query) => norm(query).split(/\s+/).filter(Boolean).every(word => norm(value).includes(word));
  function status(text, error = false) {
    $('staffing-status').textContent = text;
    $('staffing-status').classList.toggle('error-text', error);
  }
  async function api(url, options = {}) {
    if (readOnly && !['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase())) throw new Error('Доступен только просмотр данных.');
    const form = options.body instanceof FormData;
    const response = await fetch(url, {...options, cache: url === '/api/staffing/people' ? 'no-cache' : 'no-store', headers: {
      ...(!form ? {'Content-Type': 'application/json'} : {}), 'X-CSRF-Token': root.dataset.csrf, 'X-Staffing-Date': state.date }});
    const result = await response.json().catch(() => ({error: 'Не удалось прочитать ответ сервера.'}));
    if (!response.ok) { const error = new Error(result.error || 'Ошибка сохранения.'); error.status = response.status; throw error; }
    if (options.method && !url.startsWith('/api/staffing/history')) await refreshHistory();
    return result;
  }
  function canLeave() {
    if (window.mobilePlacement?.isPicking()) { status('Выберите место работы или закройте окно выбора.'); return false; }
    if (state.createCrewEditor) { status('Завершите создание бригады или нажмите «Отмена».'); return false; }
    if (state.categoryEditor) { status('Сохраните категорию ГДЛР или нажмите «Отмена» в форме.'); return false; }
    if (state.transferEditor) { status('Завершите перенос или нажмите «Отмена» в форме.'); return false; }
    if (state.employerEditor) { status('Сохраните работодателя или нажмите «Отмена» в форме.'); return false; }
    if (state.workEditor) { status('Сохраните выполняемые работы или нажмите «Отмена» в форме.'); return false; }
    if (state.busy) { status('Дождитесь завершения операции.'); return false; }
    if (state.details) { status('Завершите редактирование или нажмите «Отмена».'); return false; }
    if (state.drafts.size) { status('Выберите подобъект в изменённой строке или отмените выбор в этой строке.'); return false; }
    if (state.crewDrafts.size) { status('Сохраните назначение выбранным сотрудникам или нажмите «Отмена» в строке назначения.'); return false; }
    return true;
  }
  function busy(value) {
    state.busy = value;
    $('view-staffing').inert = value;
  }
  function calendarQuery() {
    if (!state.calendarFilter) return '';
    if (state.calendarFilter.kind === 'changes') {
      const query = new URLSearchParams({change_metric: state.calendarFilter.metric});
      for (const key of ['category', 'pps', 'author']) if (state.calendarFilter[key] !== undefined) {
        [].concat(state.calendarFilter[key]).forEach(value => query.append('change_' + key, value));
      }
      return '&' + query.toString();
    }
    const query = new URLSearchParams({calendar_sites: state.calendarFilter.sites.join(','),
      calendar_shift: MF.values(MF.get($('staffing-shift'))).length === 1 ? MF.get($('staffing-shift')) : 'all'});
    if (state.calendarFilter.contractor !== undefined) query.set('calendar_contractor', state.calendarFilter.contractor);
    if (state.calendarFilter.employer !== undefined) query.set('calendar_employer', state.calendarFilter.employer);
    if (state.calendarFilter.category !== undefined) [].concat(state.calendarFilter.category).forEach(value => query.append('calendar_category',value));
    return '&' + query.toString();
  }
  async function openFromCalendar(filter) {
    state.pps = ''; MF.set(ppsFilter, '');
    state.calendarFilter = filter;
    state.changeDirection = 'all';
    state.search = ''; state.regex = null; state.regexMode = false; state.department = ''; state.employer = ''; state.contractor = ''; state.author = ''; state.category = ''; state.unassigned = false;
    state.freshness = ''; MF.set($('staffing-freshness'), '');
    state.selected.clear();
    $('staffing-search').value = ''; $('staffing-regex').checked = false; $('staffing-unassigned').checked = false;
    $('staffing-search-error').hidden = true;
    $('staffing-date').value = filter.date; MF.set($('staffing-shift'), filter.shift);
    await load();
    $('staffing-calendar-filter').scrollIntoView({block: 'nearest'});
  }
  async function load({refreshReference = false, skipInheritance = false} = {}) {
    const expanded = new Set(displayGroups().filter(crew => !state.collapsed.has(crew.id)).map(crew => crew.id));
    const request = ++state.request;
    busy(true); status('Загрузка расстановки…');
    try {
      const [data, reference, directory, contractors, crewOptions, categories] = await Promise.all([
        api('/api/staffing?date=' + $('staffing-date').value + '&shift=all&view=summary' + calendarQuery()),
        window.appReference.get({refresh: refreshReference}), api('/api/staffing/people'), api('/api/contractors'), api('/api/staffing/crew-options'), api('/api/gdlr-categories')
      ]);
      if (request !== state.request) return;
      state.reference = reference;
      state.contractors = contractors.rows;
      state.categories = categories.rows.filter(item => item.staffing_allowed);
      state.crewOptions = crewOptions.rows;
      state.people = directory.people;
      state.peopleById = new Map(state.people.map(person => [person.id, person]));
      state.objects = new Map(reference.objects.map(o => [o.id, o]));
      state.sites = new Map(reference.subobjects.map(s => [s.id, s]));
      state.subsByObject = new Map();
      reference.subobjects.forEach(site => {
        if (!state.subsByObject.has(site.object_id)) state.subsByObject.set(site.object_id, []);
        state.subsByObject.get(site.object_id).push(site);
      });
      if (state.date !== $('staffing-date').value || state.imported?.id !== data.import?.id) state.selected.clear();
      state.rows = data.index; state.crews = data.crews; state.imported = data.import;
      rebuildItrGroups();
      if (state.calendarFilter) displayGroups().forEach(crew => expanded.add(crew.id));
      $('staffing-calendar-filter').hidden = !state.calendarFilter;
      $('staffing-shift').querySelector('option[value="none"]').disabled = !!state.calendarFilter;
      if (state.calendarFilter) $('staffing-calendar-filter-label').textContent = 'Из сводной: ' + state.calendarFilter.label +
        ' · назначений: ' + data.calendar_assignment_count + ' · сотрудников: ' + data.index.length +
        (state.calendarFilter.category !== undefined ? ' · ГДЛР: ' + (state.calendarFilter.category || 'Без категории') : '') +
        (root.dataset.role === 'foreman' ? ' · Только доступные вам бригады.' : '');
      renderChangeFilter(data.report_change);
      $('staffing-shift').disabled = !!data.report_change;
      const membership = new Map(state.rows.map(row => [row.id, row.crew_id]));
      for (const [id, crewId] of state.selected) if (membership.get(id) !== crewId) state.selected.delete(id);
      state.loadedCrews.clear(); state.loadingCrews.clear();
      state.collapsed = new Set(displayGroups().filter(crew => !expanded.has(crew.id)).map(crew => crew.id));
      state.drafts.clear(); state.crewDrafts.clear();
      state.rowsByCrew = new Map();
      const departments = new Map();
      state.rows.forEach(row => {
        if (!state.rowsByCrew.has(row.crew_id)) state.rowsByCrew.set(row.crew_id, []);
        state.rowsByCrew.get(row.crew_id).push(row);
        departments.set(row.department, (departments.get(row.department) || 0) + 1);
      });
      MF.values(state.department).forEach(key => { if (!departments.has(key)) departments.set(key, 0); });
      $('staffing-department').replaceChildren(el('option', {value: ''}, 'Все строительные участки'),
        ...[...departments].sort(([a], [b]) => a.localeCompare(b, 'ru', {numeric: true})).map(([name, count]) =>
          el('option', {value: name}, name.replace(/^Строительно[-– ]монтажный участок\s*/i, 'СМУ ') + ' · ' + count + ' чел.')));
      MF.set($('staffing-department'), state.department);
      state.date = $('staffing-date').value; state.shift = MF.get($('staffing-shift'));
      $('staffing-source').textContent = state.calendarFilter ? 'Сотрудники с фактическими назначениями по выбранной позиции.' :
        data.import ? data.import.filename + (data.import.id ? ' · лист «Явка»' : '') + (data.import.has_outstaff && data.import.id ? ' + Аутстафф' : '') : 'Импортируйте численность с листа «Явка».';
      if (data.report_change) $('staffing-source').textContent = 'Пришли — вошли в выбранную группу отчёта; ушли — вышли из неё. Сравнение за день, по обеим сменам.';
      await loadPageRows(pageRows(visibleRows()));
      if (request !== state.request) return;
      render();
      status(data.report_change ? 'Показаны только сотрудники, изменившие состав выбранного показателя относительно предыдущего дня.' : state.calendarFilter ? 'Показаны назначения за выбранную дату и смену. Фильтр позиции можно снять над таблицей.' :
        readOnly ? 'Просмотр расстановки. Раскройте группу, чтобы увидеть сотрудников и их назначения.' : data.import ? 'Для переноса с предыдущего дня отметьте сотрудников и нажмите «Перенести со вчера». Выбор подобъекта сохраняется автоматически.' : 'После импорта здесь появятся сотрудники по бригадам.');
    } catch (error) {
      state.rows = []; state.crews = []; state.drafts.clear(); state.crewDrafts.clear(); render(); status(error.message, true);
      throw error;
    } finally { if (request === state.request) busy(false); }
  }
  function renderChangeFilter(change) {
    let controls = $('staffing-change-controls');
    if (!controls) {
      controls = el('div', {id: 'staffing-change-controls', className: 'staffing-change-controls'});
      $('staffing-calendar-filter-label').after(controls);
    }
    controls.replaceChildren(); controls.hidden = !change;
    $('staffing-calendar-filter-clear').textContent = change ? 'Снять фильтр изменений' : 'Снять фильтр позиции';
    if (!change) return;
    const human = day => day.split('-').reverse().join('.');
    $('staffing-calendar-filter-label').textContent = state.calendarFilter.label + ' · ' + human(change.date) + ' к ' + human(change.previous_date);
    for (const [value, label, count] of [['all', 'Все изменения', change.arrived + change.left], ['arrived', 'Пришли', change.arrived], ['left', 'Ушли', change.left]]) {
      const button = el('button', {type: 'button', className: 'secondary-button', 'aria-pressed': String((state.changeDirection || 'all') === value)}, label + ' (' + count + ')');
      button.addEventListener('click', () => {
        if (!canLeave()) return;
        state.changeDirection = value; state.selected.clear(); renderChangeFilter(change); render();
      });
      controls.append(button);
    }
  }
  function cell(index, content, className = '') {
    return el('td', {'data-label': headers[index], className}, content);
  }
  function crewUrl(crewId) {
    return '/api/staffing?date=' + state.date + '&shift=all&crew_id=' + (crewId === null ? 'unassigned' : crewId) + calendarQuery();
  }
  function rebuildItrGroups() {
    state.rowsByItr = new Map();
    const groups = new Map();
    for (const row of state.rows) {
      const key = row.itr_group_key;
      if (!groups.has(key)) { groups.set(key, {id: key, name: row.itr_group_label, itr: true}); state.rowsByItr.set(key, []); }
      state.rowsByItr.get(key).push(row);
    }
    state.itrGroups = [...groups.values()].sort((a,b) => a.name.localeCompare(b.name, 'ru'));
    if (state.groupMode === 'hierarchy') state.hierarchy = hierarchy.build(state.rows, state.groupLevels, state.crews);
  }
  function displayGroups() { return state.groupMode === 'hierarchy' ? state.hierarchy?.nodes || [] : state.groupMode === 'itr' ? state.itrGroups : state.crews; }
  function rowsForGroup(group) { return group.hierarchical ? state.hierarchy?.byId.get(group.id)?.rows || [] : (group.itr ? state.rowsByItr : state.rowsByCrew)?.get(group.id) || []; }
  function groupKey(row) { return state.groupMode === 'hierarchy' ? state.hierarchy.paths.get(row.id)?.at(-1) : state.groupMode === 'itr' ? row.itr_group_key : row.crew_id; }
  function groupKeys(row) { return state.groupMode === 'hierarchy' ? state.hierarchy.paths.get(row.id) || [] : [groupKey(row)]; }
  function displayedGroup(row) { return displayGroups().find(group => group.id === groupKey(row)); }
  function batchSnapshot(rows) {
    return {expected_crews: Object.fromEntries(rows.map(row => [row.id, row.crew_id])),
      expected_group_tokens: Object.fromEntries(rows.map(row => [row.id, row.group_token]))};
  }
  function pageRows(filtered) {
    // Keep each leaf contiguous, including when the hierarchy is reordered.
    const signature = JSON.stringify([state.date, state.groupMode, state.groupLevels, state.search, state.regexMode,
      state.department, state.employer, state.contractor, state.pps, state.category, state.author,
      state.unassigned, state.shift, state.freshness, state.calendarFilter, state.changeDirection]);
    if (signature !== state.pageFilter) { state.page = 0; state.pageFilter = signature; }
    const ranks = new Map(displayGroups().map((group, index) => [group.id, index]));
    const ordered = [...filtered].sort((a, b) => (ranks.get(groupKey(a)) ?? 0) - (ranks.get(groupKey(b)) ?? 0));
    const info = window.TablePagination.windowFor(ordered.length, state.page, state.pageSize);
    state.page = info.page;
    return ordered.slice(info.start, info.end);
  }
  function pageCrews(rows, includeCollapsed = false) {
    const ids = new Set(rows.filter(row => includeCollapsed || groupKeys(row).every(key => !state.collapsed.has(key))).map(row => row.crew_id));
    return state.crews.filter(crew => ids.has(crew.id) && !state.loadedCrews.has(crew.id));
  }
  async function loadPageRows(rows, includeCollapsed = false) {
    const crews = pageCrews(rows, includeCollapsed);
    const request = state.request;
    for (let i = 0; i < crews.length; i += 4) {
      await Promise.all(crews.slice(i, i + 4).map(loadCrew));
      if (request !== state.request) return;
    }
  }
  async function loadGroup(group) {
    await loadPageRows(pageRows(visibleRows()).filter(row => groupKeys(row).includes(group.id)), true);
  }
  function selectedRows(crew, includeCrewless = false) {
    const rows = rowsForGroup(crew).filter(row => state.selected.has(row.id) && state.selected.get(row.id) === row.crew_id);
    return includeCrewless || rows.every(row => row.crew_id != null) ? rows : [];
  }
  function selectionBox(crew, row = null, filteredGroupRows = []) {
    const rows = row ? [row] : filteredGroupRows;
    const count = rows.filter(item => state.selected.get(item.id) === item.crew_id).length;
    const box = el('input', {type: 'checkbox', className: row ? 'staffing-select-worker' : 'staffing-select-crew',
      checked: !!rows.length && count === rows.length, indeterminate: count > 0 && count < rows.length,
      disabled: !rows.length, 'aria-label': row ? 'Выбрать сотрудника: ' + row.full_name : 'Выбрать сотрудников по фильтру: ' + crew.name,
      title: row ? row.full_name : 'Выбрать сотрудников этой группы на текущей странице (' + rows.length + ')'});
    box.addEventListener('change', () => {
      if (!canLeave()) { box.checked = count === rows.length; box.indeterminate = count > 0 && count < rows.length; return; }
      rows.forEach(item => box.checked ? state.selected.set(item.id, item.crew_id) : state.selected.delete(item.id));
      render();
      const replacement = row ? state.nodes.get(row.id)?.querySelector('.staffing-select-worker') :
        document.querySelector('[data-staffing-crew="' + crew.id + '"] .staffing-select-crew');
      replacement?.focus({preventScroll: true});
    });
    return box;
  }
  function hydrateRow(row, fresh) {
    const number = row.number;
    Object.assign(row, fresh, {number, object_id: state.sites.get(fresh.subobject_id)?.object_id || null});
    delete row.search_fields;
  }
  async function loadCrew(crew) {
    if (state.loadedCrews.has(crew.id)) return;
    if (state.loadingCrews.has(crew.id)) return state.loadingCrews.get(crew.id);
    const request = state.request;
    const pending = (async () => {
      const board = await api(crewUrl(crew.id));
      if (request !== state.request) return;
      const rows = state.rowsByCrew.get(crew.id) || [];
      const fresh = new Map(board.rows.map(row => [row.id, row]));
      if (board.import?.id !== state.imported?.id || rows.length !== fresh.size ||
          rows.some(row => !fresh.has(row.id) || fresh.get(row.id).crew_id !== crew.id || fresh.get(row.id).itr_group_key !== row.itr_group_key)) {
        throw new Error('Состав бригады изменился. Нажмите «Обновить».');
      }
      rows.forEach(row => hydrateRow(row, fresh.get(row.id)));
      const details = board.crews.find(item => item.id === crew.id);
      if (details) Object.assign(crew, details);
      state.loadedCrews.add(crew.id);
    })();
    state.loadingCrews.set(crew.id, pending);
    try { await pending; }
    finally { if (request === state.request) state.loadingCrews.delete(crew.id); }
  }
  function visibleRows() {
    return state.rows.filter(row => {
      const fields = [...(row.search_fields || [row.full_name, row.personnel_no, row.profession, row.category, row.department, row.employer,
        row.crew_name, row.object_name || '', row.subobject_name || '', row.linear_itr_name || '', row.brigadier_name || '']),
        row.assignment_author?.full_name || ''];
      return (state.calendarFilter?.kind !== 'changes' || !state.changeDirection || state.changeDirection === 'all' || row.report_change?.direction === state.changeDirection) &&
        MF.matches(state.pps, row.pps) && MF.matches(state.category, categoryKey(row)) &&
        MF.matches(state.department, row.department) && (!state.unassigned || !row.assignment_id) &&
        MF.matches(state.employer, employerKey(row)) &&
        MF.matches(state.contractor, contractorKey(row)) &&
        MF.matches(state.author, row.assignment_id ? authorKey(row) : '') &&
        MF.matches(state.freshness, row.freshness?.status) &&
        (state.calendarFilter || MF.matches(state.shift, row.employee_shift || 'none')) &&
        (state.regex ? fields.some(value => state.regex.test(value)) : matches(fields.join(' '), state.search));
    });
  }
  function employerKey(row) {
    const name = String(row.employer || '').trim();
    return name ? 'name:' + name : 'none';
  }
  function renderEmployers() {
    const counts = new Map();
    state.rows.forEach(row => {
      const key = employerKey(row);
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    MF.values(state.employer).forEach(key => { if (!counts.has(key)) counts.set(key, 0); });
    const entries = [...counts].sort(([a], [b]) => a === 'none' ? 1 : b === 'none' ? -1 : a.localeCompare(b, 'ru'));
    employerFilter.replaceChildren(el('option', {value: ''}, 'Все организации'),
      ...entries.map(([key, count]) => el('option', {value: key},
        (key === 'none' ? 'Не указана' : key.slice(5)) + ' · ' + count + ' чел.')));
    MF.set(employerFilter, state.employer);
  }
  function contractorKey(row) {
    const name = String(row.contractor || '').trim();
    return name ? 'name:' + name : 'none';
  }
  function renderContractors() {
    const counts = new Map();
    state.rows.forEach(row => {
      const key = contractorKey(row);
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    MF.values(state.contractor).forEach(key => { if (!counts.has(key)) counts.set(key, 0); });
    const entries = [...counts].sort(([a], [b]) => a === 'none' ? 1 : b === 'none' ? -1 : a.localeCompare(b, 'ru'));
    contractorFilter.replaceChildren(el('option', {value: ''}, 'Все подрядчики'),
      ...entries.map(([key, count]) => el('option', {value: key},
        (key === 'none' ? 'Не указана' : key.slice(5)) + ' · ' + count + ' чел.')));
    MF.set(contractorFilter, state.contractor);
  }
  function categoryKey(row) {
    const name = String(row.category || '').trim();
    return name ? 'name:' + name : 'none';
  }
  function renderCategories() {
    const counts = new Map();
    for (const row of state.rows) {
      const key = categoryKey(row);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    // Keep the selected category visible with zero results after data changes.
    MF.values(state.category).forEach(key => { if (!counts.has(key)) counts.set(key, 0); });
    const entries = [...counts].sort(([a], [b]) => a === 'none' ? 1 : b === 'none' ? -1 : a.localeCompare(b, 'ru'));
    $('staffing-category').replaceChildren(el('option', {value: ''}, 'Все категории'),
      ...entries.map(([key, count]) => el('option', {value: key},
        (key === 'none' ? 'Без категории' : key.slice(5)) + ' · ' + count + ' чел.')));
    MF.set($('staffing-category'), state.category);
  }
  function authorKey(row) {
    return row.assignment_author?.user_id != null ? String(row.assignment_author.user_id) : 'unknown';
  }
  function renderAuthors() {
    const authors = new Map();
    for (const row of state.rows) {
      if (!row.assignment_id) continue;
      const key = authorKey(row);
      if (!authors.has(key)) authors.set(key, {name: key === 'unknown' ? 'Автор не сохранён' :
        row.assignment_author.full_name || 'ФИО не указано', count: 0});
      authors.get(key).count++;
    }
    const names = new Map();
    for (const author of authors.values()) names.set(author.name, (names.get(author.name) || 0) + 1);
    const entries = [...authors].sort(([a, x], [b, y]) => x.name.localeCompare(y.name, 'ru') || a.localeCompare(b));
    const select = $('staffing-author');
    // Keep an active filter even if its last assignment was removed or the date changed.
    const previous = select.selectedOptions[0]?.textContent || 'Выбранный автор';
    select.replaceChildren(el('option', {value: ''}, 'Все авторы'), ...entries.map(([id, author]) =>
      el('option', {value: id}, author.name + (names.get(author.name) > 1 ? ' · №' + id : '') + ' · ' + author.count + ' чел.')));
    MF.values(state.author).filter(id => !authors.has(id)).forEach(id => select.append(el('option', {value:id}, 'Автор №' + id + ' · 0 чел.')));
    MF.set(select, state.author);
  }
  const freshnessLabels = {current: 'Актуальные', inherited: 'Со вчера', mixed: 'Частично обновлены',
    unknown: 'Источник не определён', empty: 'Нет данных на дату'};
  function renderFreshnessFilter() {
    const counts = new Map();
    for (const row of state.rows) {
      const key = row.freshness?.status || 'unknown';
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    $('staffing-freshness').replaceChildren(el('option', {value: ''}, 'Все данные'),
      ...Object.entries(freshnessLabels).map(([key, label]) => el('option', {value: key}, label + ' · ' + (counts.get(key) || 0))));
    MF.set($('staffing-freshness'), state.freshness);
  }
  function paintFreshness(tr, row) {
    const fresh = row.freshness || {status: 'unknown'};
    const badge = tr.querySelector('.staffing-freshness');
    badge.dataset.freshness = fresh.status;
    badge.textContent = freshnessLabels[fresh.status] + (fresh.source_date && fresh.status !== 'current'
      ? ' · ' + fresh.source_date.split('-').reverse().join('.') : '');
    const detail = tr.querySelector('.staffing-freshness-detail');
    detail.textContent = fresh.status === 'mixed' ? 'Со вчера: ' + fresh.inherited_fields.join(', ') + '.' :
      fresh.status === 'unknown' ? 'Старая запись без истории источника.' : '';
    detail.hidden = !detail.textContent;
    badge.title = fresh.status === 'current' ? 'Введены или изменены на выбранную дату.' :
      fresh.status === 'inherited' ? 'Перенесены с предыдущего дня, ещё не обновлялись.' : detail.textContent;
  }
  $('staffing-freshness').addEventListener('change', () => {
    if (!canLeave()) { MF.set($('staffing-freshness'), state.freshness); return; }
    state.freshness = MF.get($('staffing-freshness'));
    if (!state.calendarFilter) preferences.set({freshness: state.freshness});
    render();
  });
  function mobileSelection(filtered = visibleRows()) {
    const rows = state.rows.filter(row => state.selected.get(row.id) === row.crew_id);
    const visible = new Set(filtered.map(row => row.id));
    return {count: rows.length, hidden: rows.filter(row => !visible.has(row.id)).length,
      filters: ['department', 'employer', 'contractor', 'author', 'category', 'pps', 'freshness', 'shift', 'search', 'unassigned'].filter(key => !!state[key]).length};
  }
  function updateTotals() {
    renderEmployers();
    renderContractors();
    renderCategories();
    renderAuthors();
    renderFreshnessFilter();
    const filtered = visibleRows();
    const selected = state.rows.filter(row => state.selected.get(row.id) === row.crew_id);
    document.dispatchEvent(new CustomEvent('staffing-selection-change', {detail: mobileSelection(filtered)}));
    if ($('staffing-create-crew')) $('staffing-create-crew').textContent = selected.length ?
      '+ Создать бригаду из выбранных (' + selected.length + ')' : '+ Создать бригаду';
    const unsupported = !selected.length || selected.some(row => row.crew_id == null);
    $('staffing-clear-selected').disabled = !selected.length;
    $('staffing-clear-selected').textContent = 'Сбросить расстановку (' + selected.length + ')';
    // Collapsed crews contain summary rows; the editor loads and validates full details.
    $('staffing-employer-selected').disabled = !selected.length;
    $('staffing-employer-selected').textContent = 'Работодатель (' + selected.length + ')';
    $('staffing-work-selected').disabled = !selected.length;
    $('staffing-work-selected').textContent = 'Выполняемые работы (' + selected.length + ')';
    $('staffing-category-selected').disabled = unsupported;
    $('staffing-category-selected').textContent = 'Изменить ГДЛР (' + selected.length + ')';
    $('staffing-transfer-selected').disabled = !selected.length;
    $('staffing-transfer-selected').textContent = 'Перенести со вчера (' + selected.length + ')';
    $('staffing-transfer-tomorrow').disabled = !selected.length || state.date === '9999-12-31';
    $('staffing-transfer-tomorrow').textContent = 'Перенести на завтра (' + selected.length + ')';
    const assigned = filtered.filter(row => row.assignment_id).length;
    $('staffing-stats').replaceChildren(...[['Сотрудников', filtered.length], ['Расставлено', assigned],
      ['Без назначения', filtered.length - assigned]].map(([label, count]) =>
      el('div', {}, el('strong', {}, String(count)), el('span', {}, label))));
    const counts = new Map();
    filtered.forEach(row => {
      for (const key of groupKeys(row)) {
        if (!counts.has(key)) counts.set(key, {total: 0, assigned: 0});
        const count = counts.get(key); count.total++; count.assigned += Number(!!row.assignment_id);
      }
    });
    const selectedCounts = new Map();
    for (const row of selected) for (const key of groupKeys(row)) selectedCounts.set(key, (selectedCounts.get(key) || 0) + 1);
    document.querySelectorAll('[data-crew-count]').forEach(node => {
      const crewId = state.groupMode !== 'crew' ? node.dataset.crewCount : node.dataset.crewCount === 'null' ? null : Number(node.dataset.crewCount);
      const count = counts.get(crewId);
      const selected = selectedCounts.get(crewId) || 0;
      node.textContent = (count ? count.total + ' чел. · расставлено ' + count.assigned + (Number(node.dataset.pageCount) < count.total ? ' · на странице ' + node.dataset.pageCount : '') : '0 чел.') + (selected ? ' · выбрано ' + selected : '');
    });
    $('staffing-total').textContent = 'По фильтру: ' + filtered.length + (state.groupMode === 'hierarchy' ? ' чел., групп: ' : state.groupMode === 'itr' ? ' чел., групп ИТР: ' : ' чел., групп бригад: ') + counts.size +
      (state.calendarFilter?.kind === 'changes' ? '. Всего с изменениями: ' : state.calendarFilter ? '. Всего по выбранной позиции: ' : '. Всего в импортированной численности: ') + state.rows.length + ' чел.';
  }
  function objectOptions() {
    const grouped = new Map();
    for (const item of state.reference.objects) {
      const key = item.stage_id || null;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(el('option', {value: String(item.id)}, item.name));
    }
    return [...(state.reference.stages || []).map(stage => el('optgroup', {label: stage.name},
      ...(grouped.get(stage.id) || [el('option', {disabled: true}, 'Группы ещё не добавлены')]))),
      ...(grouped.get(null) || [])];
  }
  function pickMobileLocation(name, currentObject, currentSite, onChoose) {
    if (!canLeave() || !window.mobilePlacement) return;
    const stages = new Map((state.reference.stages || []).map(stage => [stage.id, stage.name]));
    window.mobilePlacement.pickLocation({name,
      objects: state.reference.objects.map(item => ({...item, label: (stages.get(item.stage_id) ? stages.get(item.stage_id) + ' · ' : '') + item.name})),
      sites: [...state.sites.values()], currentObject, currentSite, onChoose});
  }
  function paintPlace(row, message = '', error = false) {
    const tr = state.nodes.get(row.id);
    if (!tr) return;
    const assigned = !!row.assignment_id;
    tr.classList.toggle('staffing-row-assigned', assigned);
    tr.classList.toggle('staffing-row-absent', row.attendance_status !== 'Явка');
    paintFreshness(tr, row);
    tr.querySelector('.staffing-placement-badge').hidden = !assigned;
    const author = tr.querySelector('.staffing-assignment-author');
    author.replaceChildren();
    if (assigned) {
      if (row.assignment_author) {
        const stamp = new Date(row.assignment_author.changed_at);
        const formatted = new Intl.DateTimeFormat('ru-RU', {timeZone: 'Europe/Moscow',
          day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'}).format(stamp);
        author.append(el('span', {}, row.assignment_author.full_name || 'ФИО учётной записи не указано'),
          el('time', {dateTime: row.assignment_author.changed_at, title: 'Время назначения по Москве'}, formatted + ' МСК'));
      } else author.textContent = 'Автор и время назначения не сохранены';
    } else author.textContent = '—';
    columnSettings.cell(tr, 10).replaceChildren(shiftControl(state.crews.find(crew => crew.id === row.crew_id), row));
    columnSettings.cell(tr, 11).replaceChildren(attendanceControl(state.crews.find(crew => crew.id === row.crew_id), row));
    columnSettings.cell(tr, 12).replaceChildren(workControl(null, row));
    const draft = state.drafts.get(row.id);
    const objectId = draft ? draft.objectId : row.object_id;
    const object = state.objects.get(objectId);
    if (readOnly) {
      columnSettings.cell(tr, 1).textContent = object?.name || '—';
      columnSettings.cell(tr, 2).textContent = state.sites.get(row.subobject_id)?.name || 'Без назначения';
      return;
    }
    const objectSelect = el('select', {className: 'staffing-object-select', disabled: row.locked || !row.employee_shift || !!draft?.uncertain,
      'aria-label': 'Группа подобъектов: ' + row.full_name, title: object?.name || 'Выберите группу подобъектов'},
      el('option', {value: '', disabled: true}, 'Выберите группу'), ...objectOptions());
    objectSelect.value = objectId ? String(objectId) : '';
    objectSelect.addEventListener('change', () => {
      if (state.busy || state.details || state.crewDrafts.size) {
        objectSelect.value = objectId ? String(objectId) : ''; canLeave(); return;
      }
      const next = Number(objectSelect.value);
      if (next === row.object_id) state.drafts.delete(row.id);
      else state.drafts.set(row.id, {objectId: next});
      paintPlace(row);
      const nextSelect = state.nodes.get(row.id)?.querySelector('.staffing-subobject-select');
      nextSelect?.focus({preventScroll: true});
    });
    const site = !draft ? state.sites.get(row.subobject_id) : null;
    const subSelect = el('select', {className: 'staffing-subobject-select', disabled: row.locked || !row.employee_shift || !objectId || !!draft?.uncertain,
      'aria-label': 'Подобъект: ' + row.full_name, title: site?.name || (objectId ? 'Выберите подобъект' : 'Сначала выберите группу')});
    const populate = (allOptions) => {
      const options = allOptions ? state.subsByObject.get(objectId) || [] : (site ? [site] : []);
      subSelect.replaceChildren(el('option', {value: '', disabled: true}, objectId ? 'Выберите подобъект' : 'Сначала выберите группу'),
        ...options.map(item => el('option', {value: String(item.id)}, item.name)),
        ...(row.assignment_id ? [el('option', {value: 'clear'}, 'Без назначения')] : []));
      subSelect.value = site ? String(site.id) : '';
    };
    // Existing assignments only need their current option until the user opens the list.
    populate(!!draft);
    subSelect.addEventListener('focus', () => populate(true), {once: true});
    subSelect.addEventListener('change', () => {
      if (state.busy || state.details || state.crewDrafts.size) { canLeave(); populate(true); return; }
      const selected = subSelect.value;
      if (!selected) return;
      saveRowPlace(row, selected === 'clear' ? null : Number(selected));
    });
    const objectCell = columnSettings.cell(tr, 1), subCell = columnSettings.cell(tr, 2);
    objectCell.replaceChildren(objectSelect, el('button', {type: 'button', className: 'secondary-button staffing-location-open',
      disabled: row.locked || !row.employee_shift || !!draft, 'aria-label': 'Выбрать место работы: ' + row.full_name,
      onclick: () => {
        pickMobileLocation(row.full_name, row.object_id, row.subobject_id, site => saveRowPlace(row, site));
      }}, assigned ? 'Изменить место' : 'Выбрать место'));
    subCell.replaceChildren(subSelect);
    if (object) objectCell.append(el('small', {className: 'staffing-selected-label'}, object.name));
    if (site) subCell.append(el('small', {className: 'staffing-selected-label'}, site.name));
    if (draft) {
      subCell.append(el('small', {className: 'staffing-row-status'}, draft.uncertain ? 'Проверяется состояние назначения' : 'Выберите подобъект · не сохранено'),
        el('button', {className: 'text-button staffing-cancel-row', onclick: async () => {
          if (state.busy) return;
          if (draft.uncertain) {
            busy(true);
            try { await refreshRow(row); state.drafts.delete(row.id); paintPlace(row); updateTotals(); if (state.author || state.freshness) render(); }
            catch (problem) { paintPlace(row, problem.message, true); }
            finally { busy(false); }
          } else { state.drafts.delete(row.id); paintPlace(row); }
        }}, draft.uncertain ? 'Обновить строку' : 'Отменить выбор'));
    }
    if (message) subCell.append(el('small', {className: 'staffing-row-status' + (error ? ' error-text' : ''), role: 'status'}, message));
  }
  async function refreshRow(row) {
    const board = await api(crewUrl(row.crew_id));
    const fresh = board.rows.find(member => member.id === row.id && member.crew_id === row.crew_id);
    if (!fresh) throw new Error('Состав бригады изменился. Обновите таблицу.');
    hydrateRow(row, fresh);
  }
  async function saveRowPlace(row, siteId) {
    if (state.busy) return;
    busy(true);
    const pendingObject = state.drafts.get(row.id)?.objectId || row.object_id;
    state.drafts.set(row.id, {objectId: pendingObject, uncertain: true});
    paintPlace(row, 'Сохранение…');
    let saved = false;
    try {
      await api(row.crew_id == null ? '/api/staffing/groups/assignments' : '/api/staffing/crews/' + row.crew_id + '/assignments', {method: 'PUT', body: JSON.stringify({
        date: state.date, worker_ids: [row.id], subobject_id: siteId,
        expected_tokens: {[row.id]: row.day_token}, ...(row.crew_id == null ? batchSnapshot([row]) : {})})});
      saved = true;
      if (state.calendarFilter) {
        state.drafts.delete(row.id);
        await load(); status('Сохранено: ' + row.full_name + '.'); return;
      }
      await refreshRow(row);
      state.drafts.delete(row.id);
      paintPlace(row, 'Сохранено');
      paintCrewPlace(displayedGroup(row));
      updateTotals();
      status('Сохранено: ' + row.full_name + '.');
      if (state.author || state.freshness || (state.unassigned && row.assignment_id)) render();
    } catch (problem) {
      let refreshed = false;
      try { await refreshRow(row); refreshed = true; state.drafts.delete(row.id); } catch (_) { /* Keep the unresolved row visible. */ }
      const message = saved ? 'Назначение сохранено. Не удалось обновить строку.' : problem.message;
      paintPlace(row, message + (refreshed ? ' Показано текущее назначение.' : ' Обновите строку или таблицу перед следующим изменением.'), true);
      if (refreshed) paintCrewPlace(displayedGroup(row));
      updateTotals(); status(message, true);
      if (refreshed && (state.author || state.freshness)) render();
    } finally { busy(false); }
  }
  function crewPlaceRow(crew, visibleCount = null) {
    const tr = el('tr', {className: 'staffing-crew-assignment'});
    tr.append(cell(0, '', 'staffing-crew-caption'), cell(1, null), cell(2, null),
      cell(3, groupContractorControl(crew)), el('td', {'data-label': 'Новая бригада'}),
      el('td', {colSpan: 2, className: 'staffing-crew-actions'}), cell(7, selectedCategoryButton(crew)),
      cell(8, responsibleButton(crew, null, 'linear_itr')), cell(9, responsibleButton(crew, null, 'brigadier')), cell(10, shiftControl(crew)), cell(11, attendanceControl(crew)), cell(12, workControl(crew)), cell(13, '—', 'staffing-secondary'));
    state.crewNodes.set(crew.id, tr);
    paintCrewPlace(crew, '', false, visibleCount);
    return tr;
  }
  function paintCrewPlace(crew, message = '', error = false, visibleCount = null) {
    if (!crew) return;
    const tr = state.crewNodes.get(crew.id);
    if (!tr) return;
    const rows = selectedRows(crew, true);
    const selectedCount = selectedRows(crew, true).length;
    tr.classList.toggle('mobile-group-selected', selectedCount > 0);
    columnSettings.cell(tr, 0).textContent = selectedCount ? 'Выбрано: ' + selectedCount : 'Выберите сотрудников';
    columnSettings.cell(tr, 0).append(el('button', {type: 'button', className: 'text-button mobile-group-more',
      'aria-expanded': String(tr.classList.contains('mobile-group-expanded')), onclick: event => {
        const expanded = tr.classList.toggle('mobile-group-expanded');
        event.currentTarget.setAttribute('aria-expanded', String(expanded));
        event.currentTarget.textContent = expanded ? 'Скрыть дополнительные действия' : 'Все действия группы';
      }}, tr.classList.contains('mobile-group-expanded') ? 'Скрыть дополнительные действия' : 'Все действия группы'));
    columnSettings.cell(tr, 3).replaceChildren(groupContractorControl(crew));
    columnSettings.cell(tr, 4).replaceChildren(groupCrewControl(crew));
    columnSettings.cell(tr, 7).replaceChildren(responsibleButton(crew, null, 'linear_itr'));
    columnSettings.cell(tr, 8).replaceChildren(responsibleButton(crew, null, 'brigadier'));
    columnSettings.cell(tr, 9).replaceChildren(shiftControl(crew));
    columnSettings.cell(tr, 10).replaceChildren(attendanceControl(crew));
    columnSettings.cell(tr, 11).replaceChildren(workControl(crew));
    columnSettings.cell(tr, 6).replaceChildren(selectedCategoryButton(crew));
    const draft = state.crewDrafts.get(crew.id);
    const sharedObject = rows.length && rows.every(row => row.object_id && row.object_id === rows[0].object_id) ? rows[0].object_id : null;
    const objectId = draft ? draft.objectId : sharedObject;
    const sharedSite = rows.length && rows.every(row => row.subobject_id && row.subobject_id === rows[0].subobject_id) ? rows[0].subobject_id : null;
    const object = state.objects.get(objectId);
    const disabled = !rows.length || rows.some(row => row.locked || !row.employee_shift);
    const begin = () => ({objectId, workerIds: rows.map(row => row.id), snapshot: batchSnapshot(rows), expected: Object.fromEntries(rows.map(row => [row.id, row.day_token]))});
    const objectSelect = el('select', {className: 'staffing-crew-object', disabled,
      'aria-label': 'Группа подобъектов для выбранных: ' + crew.name, title: object?.name || 'Выберите группу'},
      el('option', {value: '', disabled: true}, 'Выберите группу'), ...objectOptions());
    objectSelect.value = objectId ? String(objectId) : '';
    objectSelect.addEventListener('change', () => {
      if (state.busy || state.details || state.drafts.size) { objectSelect.value = objectId ? String(objectId) : ''; canLeave(); return; }
      state.crewDrafts.set(crew.id, {...begin(), objectId: Number(objectSelect.value)});
      paintCrewPlace(crew);
    });
    const selectedSite = draft ? (Object.hasOwn(draft, 'siteId') ? draft.siteId : undefined) : sharedSite;
    const subSelect = el('select', {className: 'staffing-crew-subobject', disabled: disabled || !objectId,
      'aria-label': 'Подобъект для выбранных: ' + crew.name,
      title: state.sites.get(selectedSite)?.name || 'Выберите подобъект'},
      el('option', {value: '', disabled: true}, objectId ? 'Выберите подобъект' : 'Сначала выберите группу'),
      ...(state.subsByObject.get(objectId) || []).map(site => el('option', {value: String(site.id)}, site.name)),
      ...(rows.some(row => row.assignment_id) ? [el('option', {value: 'clear'}, 'Без назначения')] : []));
    subSelect.value = selectedSite ? String(selectedSite) : (draft && selectedSite === null ? 'clear' : '');
    subSelect.addEventListener('change', () => {
      if (state.busy || state.details || state.drafts.size) { canLeave(); paintCrewPlace(crew); return; }
      state.crewDrafts.set(crew.id, {...(draft || begin()), siteId: subSelect.value === 'clear' ? null : Number(subSelect.value)});
      paintCrewPlace(crew);
    });
    columnSettings.cell(tr, 1).replaceChildren(objectSelect, el('button', {type: 'button',
      className: 'secondary-button staffing-location-open', disabled,
      'aria-label': 'Место работы выбранных: ' + crew.name, onclick: () => {
        const snapshot = begin();
        pickMobileLocation('Выбранные сотрудники: ' + rows.length, objectId, sharedSite, siteId => {
          state.crewDrafts.set(crew.id, {...snapshot, objectId: siteId == null ? objectId : state.sites.get(siteId)?.object_id, siteId});
          saveCrewPlace(crew);
        });
      }}, 'Выбрать место (' + rows.length + ')'));
    columnSettings.cell(tr, 2).replaceChildren(subSelect);
    if (object) columnSettings.cell(tr, 1).append(el('small', {className: 'staffing-selected-label'}, object.name));
    if (selectedSite) columnSettings.cell(tr, 2).append(el('small', {className: 'staffing-selected-label'}, state.sites.get(selectedSite)?.name || ''));
    const ready = draft && Object.hasOwn(draft, 'siteId');
    const visibleIds = new Set(visibleRows().map(row => row.id));
    const hiddenCount = rows.filter(row => !visibleIds.has(row.id)).length;
    columnSettings.cell(tr, 5).replaceChildren(el('div', {className: 'staffing-crew-apply'},
      el('button', {className: 'secondary-button', disabled: disabled || !ready,
        onclick: () => saveCrewPlace(crew)}, (ready && draft.siteId === null ? 'Снять назначение выбранным' : 'Назначить выбранным') + ' (' + rows.length + ' чел.)'),
      el('span', {}, rows.length ? 'Только отмеченные в этой группе: ' + rows.length + ' чел.' + (hiddenCount ? ' Скрыты фильтром: ' + hiddenCount + '.' : '') : 'Отметьте сотрудников или выберите всю группу чекбоксом в её заголовке.'),
      ...(draft ? [el('button', {className: 'text-button', onclick: () => {
        if (state.busy) return;
        state.crewDrafts.delete(crew.id); paintCrewPlace(crew);
      }}, 'Отмена')] : []),
      ...(rows.some(row => row.shift_conflict) ? [el('small', {className: 'error-text'}, 'У сотрудника несколько назначений за дату. Уточните их в разделе «Состав бригад».')] :
        rows.some(row => row.locked) ? [el('small', {className: 'error-text'}, 'В бригаде есть сотрудник с назначением другой бригады. Общее назначение недоступно.')] : []),
      ...(message ? [el('small', {className: 'staffing-row-status' + (error ? ' error-text' : ''), role: 'status'}, message)] : [])));
  }
  async function saveCrewPlace(crew) {
    const draft = state.crewDrafts.get(crew.id);
    if (state.busy || !draft || !Object.hasOwn(draft, 'siteId')) return;
    const targets = new Set(draft.workerIds);
    const rows = rowsForGroup(crew).filter(row => targets.has(row.id));
    if (rows.length !== targets.size) { status('Состав бригады изменился. Обновите таблицу.', true); return; }
    busy(true);
    let saved = false;
    try {
      await api(crew.itr || crew.id == null ? '/api/staffing/groups/assignments' : '/api/staffing/crews/' + crew.id + '/assignments', {method: 'PUT', body: JSON.stringify({
        date: state.date, worker_ids: rows.map(row => row.id), subobject_id: draft.siteId,
        expected_tokens: draft.expected, ...(crew.itr || crew.id == null ? draft.snapshot : {})})});
      saved = true;
      if (state.calendarFilter || crew.itr) {
        state.crewDrafts.delete(crew.id);
        await load(); status('Сохранено: ' + rows.length + ' чел.'); return;
      }
      const board = await api(crewUrl(crew.id));
      const fresh = new Map(board.rows.filter(row => row.crew_id === crew.id).map(row => [row.id, row]));
      if (rows.some(row => !fresh.has(row.id))) throw new Error('Состав бригады изменился. Обновите таблицу.');
      rows.forEach(row => {
        const member = fresh.get(row.id);
        hydrateRow(row, member);
        paintPlace(row, 'Сохранено');
      });
      state.crewDrafts.delete(crew.id);
      paintCrewPlace(crew, 'Сохранено: ' + rows.length + ' чел.');
      updateTotals(); status('Сохранено: ' + crew.name + ', ' + rows.length + ' чел.');
      if (state.unassigned || state.author || state.freshness) render();
    } catch (problem) {
      // Server tokens still protect retries; keep the intent until explicit cancel or retry.
      const message = saved ? 'Назначение сохранено, но таблицу не удалось обновить. Повторите операцию или обновите таблицу после отмены выбора.' : problem.message;
      paintCrewPlace(crew, message, true); status(message, true);
    } finally { busy(false); }
  }
  async function clearSelected() {
    if (!canLeave()) return;
    const rows = state.rows.filter(row => state.selected.get(row.id) === row.crew_id);
    if (!rows.length) return;
    const crewIds = new Set(rows.map(row => row.crew_id));
    busy(true);
    let saved = false;
    try {
      await Promise.all(state.crews.filter(crew => crewIds.has(crew.id)).map(loadCrew));
      if (rows.some(row => row.locked || !row.day_token || !row.employee_shift)) {
        throw new Error('У отмеченного сотрудника конфликт назначений. Уточните его в разделе «Состав бригад».');
      }
      const visibleIds = new Set(visibleRows().map(row => row.id));
      const hidden = rows.filter(row => !visibleIds.has(row.id)).length;
      const assigned = rows.filter(row => row.assignment_id).length;
      if (!assigned) { status('У отмеченных сотрудников нет назначений на выбранную дату.'); return; }
      const day = state.date.split('-').reverse().join('.');
      if (!window.confirm('Сбросить расстановку за ' + day + ' у отмеченных сотрудников?\n' +
          'Отмечено: ' + rows.length + ', с назначением: ' + assigned + '.' +
          (hidden ? '\nСкрыты текущими фильтрами: ' + hidden + '. Они тоже включены в сброс.' : '') +
          '\nБудут сняты места работы. Смены, бригады и ответственные сохранятся.')) return;
      const result = await api('/api/staffing/assignments/clear', {method: 'PUT', body: JSON.stringify({
        date: state.date, worker_ids: rows.map(row => row.id),
        expected_crews: Object.fromEntries(rows.map(row => [row.id, row.crew_id])),
        expected_tokens: Object.fromEntries(rows.map(row => [row.id, row.day_token]))})});
      saved = true;
      state.selected.clear();
      await load();
      status('Расстановка за ' + day + ' сброшена: ' + result.cleared + ' чел.');
    } catch (error) {
      status(saved ? 'Расстановка сброшена, но таблицу не удалось обновить. Нажмите «Обновить».' : error.message, true);
    } finally { busy(false); }
  }
  function render() {
    rebuildItrGroups();
    const filtered = visibleRows();
    const page = pageRows(filtered);
    const pageIds = new Set(page.map(row => row.id));
    const elsewhere = [...state.selected.keys()].filter(id => !pageIds.has(id)).length;
    pager.update(filtered.length, state.page, state.pageSize, elsewhere ? 'Выбрано вне этой страницы: ' + elsewhere + '. Массовые действия учитывают всех отмеченных.' : '');
    if (pageCrews(page).length) {
      if (state.pageLoading) return;
      state.pageLoading = true;
      const request = state.request;
      const wasBusy = state.busy;
      busy(true); status('Загрузка страницы…');
      $('staffing-table').replaceChildren(el('div', {className: 'empty-state', role: 'status'}, 'Загрузка страницы…'));
      loadPageRows(page).then(() => {
        if (request === state.request) { state.pageLoading = false; render(); status('Страница загружена.'); }
      }).catch(error => {
        if (request === state.request) {
          page.forEach(row => groupKeys(row).forEach(key => state.collapsed.add(key)));
          state.pageLoading = false; render(); status(error.message + ' Повторите раскрытие группы.', true);
        }
      }).finally(() => { state.pageLoading = false; if (request === state.request) busy(wasBusy); });
      return;
    }
    const groups = new Map();
    state.nodes.clear();
    state.crewNodes.clear();
    page.forEach(row => { for (const key of groupKeys(row)) { if (!groups.has(key)) groups.set(key, []); groups.get(key).push(row); } });
    const table = el('table', {className: 'staffing-table'}, el('thead', {}, el('tr', {}, ...headers.map(h => el('th', {scope: 'col'}, h)))));
    displayGroups().filter(crew => groups.has(crew.id) &&
      (!crew.hierarchical || !crew.ancestorIds.some(id => state.collapsed.has(id)))).forEach(crew => {
      const rows = groups.get(crew.id);
      const body = el('tbody', {'data-staffing-crew': String(crew.id)});
      if (crew.hierarchical) {
        body.classList.add('staffing-hierarchy-group');
        body.classList.toggle('hierarchy-parent', !!crew.children.length);
        body.style.setProperty('--group-depth', crew.depth);
        body.dataset.groupLevel = crew.field;
      }
      const expanded = !state.collapsed.has(crew.id);
      const toggle = el('button', {className: 'staffing-group-toggle', 'aria-expanded': String(expanded),
        'aria-label': 'Развернуть или свернуть ' + crew.name, onclick: async () => {
          if (!canLeave()) return;
          if (!state.collapsed.has(crew.id)) { state.collapsed.add(crew.id); render(); return; }
          const request = state.request;
          busy(true); toggle.setAttribute('aria-busy', 'true'); status('Загрузка состава: ' + crew.name + '…');
          try {
            await loadGroup(crew);
            if (request !== state.request) return;
            state.collapsed.delete(crew.id); render(); status('Состав загружен: ' + crew.name + '.');
          } catch (error) { status(error.message + ' Повторите раскрытие бригады.', true); }
          finally {
            toggle.removeAttribute('aria-busy');
            if (request === state.request) busy(false);
          }
        }}, el('span', {className: 'staffing-outline-icon', 'aria-hidden': 'true'}, expanded ? '−' : '+'), crew.name);
      body.append(el('tr', {className: 'staffing-group-row'}, el('th', {colSpan: headers.length, scope: 'rowgroup'},
        el('div', {className: 'staffing-group-content'}, el('label', {className: 'check-label'}, selectionBox(crew, null, rows)), toggle,
          el('span', {'data-crew-count': String(crew.id), 'data-page-count': rows.length}, rows.length + ' чел.'),
          ...(!readOnly && crew.itr ? [el('small', {}, 'Бригад: ' + new Set(rows.map(row => row.crew_id).filter(Boolean)).size)] :
            readOnly ? [] : [el('button', {className: 'text-button', disabled: !crew.id || crew.can_edit_whole === false,
              title: crew.can_edit_whole === false ? 'В бригаде есть сотрудники других СМУ. Выберите доступных сотрудников для изменения ответственных.' : '',
              onclick: () => openDetails(crew)}, 'Ответственные бригады')])))));
      if (expanded && (!crew.hierarchical || !crew.children.length)) { if (!readOnly) body.append(crewPlaceRow(crew, rows.length)); rows.forEach(row => body.append(renderRow(row, state.crews.find(item => item.id === row.crew_id)))); }
      table.append(body);
    });
    $('staffing-table').replaceChildren(filtered.length ? table : el('div', {className: 'empty-state'}, 'Нет сотрудников по выбранным условиям.'));
    if (filtered.length) columnSettings.attach(table);
    updateTotals();
  }
  function renderRow(row, crew) {
    const tr = el('tr', {className: 'staffing-worker-row' + (state.selected.get(row.id) === crew.id ? ' staffing-row-selected' : ''), 'data-worker-id': String(row.id)});
    tr.append(cell(0, el('label', {className: 'check-label'}, selectionBox(crew, row), String(row.number)), 'staffing-number'), cell(1, null), cell(2, null),
      cell(3, contractorControl(row), 'staffing-secondary'), cell(4, employerControl(row), 'staffing-secondary'),
      cell(5, [el('strong', {}, row.full_name), row.pps ? el('small', {className: 'staffing-worker-crew'}, row.pps) : null,
        row.report_change ? el('span', {className: 'staffing-change-badge ' + row.report_change.direction}, row.report_change.direction === 'arrived' ? 'Пришёл' : 'Ушёл') : null,
        row.report_change ? el('small', {className: 'staffing-worker-crew'}, row.report_change.previous_status + ' → ' + row.report_change.current_status) : null,
        el('span', {className: 'staffing-placement-badge', hidden: true}, 'Расставлен'),
        state.groupMode !== 'crew' ? el('small', {className: 'staffing-worker-crew'}, row.crew_name || 'Без бригады') : null,
        el('span', {className: 'staffing-freshness'}), el('small', {className: 'staffing-freshness-detail'})], 'staffing-name'), cell(6, row.personnel_no),
      cell(7, selectedCategoryButton(crew, row), 'staffing-secondary'),
      cell(8, responsibleButton(crew, row, 'linear_itr'), 'staffing-secondary'),
      cell(9, responsibleButton(crew, row, 'brigadier')), cell(10, shiftControl(crew, row)), cell(11, attendanceControl(crew, row)), cell(12, workControl(null, row)),
      cell(13, el('small', {className: 'staffing-assignment-author'}), 'staffing-secondary'));
    const more = el('button', {className: 'staffing-more text-button', 'aria-expanded': 'false', onclick: () => {
      const expanded = tr.classList.toggle('show-all'); more.setAttribute('aria-expanded', String(expanded));
      more.textContent = expanded ? 'Скрыть дополнительные поля' : 'Все поля';
    }}, 'Все поля');
      columnSettings.cell(tr, 0).append(more);
      if (row.departure_warning) columnSettings.cell(tr, 0).append(el('small', {className: 'departure-warning', title: row.departure_warning}, '⚠ ' + row.departure_warning));
    if (row.locked && !readOnly) columnSettings.cell(tr, 0).append(el('small', {}, row.shift_conflict ? 'Несколько смен: уточните в составе бригад' : !row.active ? 'Сотрудник отключён' : 'Назначен другой бригадой'));
    state.nodes.set(row.id, tr);
    paintPlace(row);
    return tr;
  }
  function selectedCategoryButton(crew, row = null) {
    if (readOnly) return row?.category || '—';
    const targets = row ? [row] : selectedRows(crew);
    const value = targets.length && targets.every(item => item.category === targets[0].category) ? targets[0].category : '';
    return el('button', {className: 'staffing-cell-button', disabled: !crew.id || !targets.length,
      onclick: () => openSelectedCategory(targets),
      'aria-label': 'Категория ГДЛР' + (row ? ': ' + row.full_name : ' для выбранных: ' + crew.name),
      title: (value || 'Выбрать категорию') + (row ? ' · Корректировка сотрудника' : ' · Только выбранным сотрудникам')},
      value || 'Выбрать категорию');
  }
  async function openSelectedCategory(selectedTargets = null) {
    if (!canLeave()) return;
    const targets = selectedTargets || state.rows.filter(row => row.crew_id != null && state.selected.get(row.id) === row.crew_id);
    if (!targets.length) return;
    const ids = new Set(targets.map(row => row.id));
    busy(true); status('Загрузка категорий ГДЛР…');
    let catalog;
    try {
      const crewIds = new Set(targets.map(row => row.crew_id));
      [catalog] = await Promise.all([api('/api/gdlr-categories'),
        ...state.crews.filter(crew => crewIds.has(crew.id)).map(loadCrew)]);
    } catch (error) { status(error.message, true); return; }
    finally { busy(false); }
    const rows = state.rows.filter(row => ids.has(row.id));
    if (rows.length !== ids.size || rows.some(row => !row.active)) {
      status('Список сотрудников изменился. Обновите расстановку.', true); return;
    }
    const snapshot = {worker_ids: rows.map(row => row.id), ...batchSnapshot(rows),
      expected_tokens: Object.fromEntries(rows.map(row => [row.id, row.category_binding_token]))};
    const select = el('select', {id: 'staffing-category-choice'}, el('option', {value: '', disabled: true}, 'Выберите категорию'),
      ...catalog.rows.filter(item => item.active && item.staffing_allowed).map(item => el('option', {value: String(item.id)}, item.name)));
    select.value = '';
    const message = el('p', {role: 'status'}, catalog.rows.some(item => item.active) ? '' : 'В справочнике нет доступных категорий.');
    const close = () => { if (state.busy) return; state.categoryEditor = null; dialog.close(); dialog.remove(); };
    const cancel = el('button', {type: 'button', className: 'secondary-button', onclick: close}, 'Отмена');
    const save = el('button', {type: 'button', className: 'primary-button', disabled: true}, 'Сохранить категорию (' + rows.length + ')');
    select.addEventListener('change', () => { save.disabled = !select.value; });
    const visible = new Set(visibleRows().map(row => row.id));
    const hidden = rows.filter(row => !visible.has(row.id)).length;
    const dialog = el('dialog', {className: 'staffing-work-dialog', 'aria-labelledby': 'staffing-category-title'},
      el('h2', {id: 'staffing-category-title'}, 'Категория ГДЛР выбранных сотрудников'),
      el('p', {}, 'Выбрано: ' + rows.length + ' чел.' + (hidden ? ' Скрыты фильтром: ' + hidden + ' чел.' : '')),
      el('p', {}, 'Новая категория заменит текущую у всех выбранных сотрудников. Изменение действует для всех дат и сохраняется при повторном импорте.'),
      el('label', {htmlFor: select.id}, 'Новая категория ГДЛР'), select,
      el('ul', {className: 'staffing-transfer-list'}, ...rows.map(row => el('li', {},
        el('strong', {}, row.full_name + ' · ' + row.personnel_no), el('span', {}, 'Сейчас: ' + (row.category || 'Не указана'))))),
      message, el('div', {className: 'staffing-work-actions'}, cancel, save));
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    save.addEventListener('click', async () => {
      if (state.busy) return;
      const category = catalog.rows.find(item => String(item.id) === select.value && item.active);
      if (!category) return;
      busy(true); save.disabled = true; cancel.disabled = true; select.disabled = true;
      let saved = false;
      try {
        const result = await api('/api/staffing/groups/category', {method: 'PUT', body: JSON.stringify({
          ...snapshot, category_id: category.id, category_token: category.edit_token})});
        saved = true; state.categoryEditor = null; dialog.close(); dialog.remove();
        await load({skipInheritance: true});
        status('Категория ГДЛР сохранена: ' + result.category + ' · ' + result.saved + ' чел.');
      } catch (error) {
        if (saved) status('Категория сохранена. Не удалось обновить таблицу: ' + error.message, true);
        else { message.textContent = error.message; message.classList.add('error-text'); }
      } finally { busy(false); save.disabled = false; cancel.disabled = false; select.disabled = false; }
    });
    state.categoryEditor = dialog; document.body.append(dialog); dialog.showModal(); select.focus();
  }
  async function openSelectedTransfer(direction = 'previous') {
    if (!canLeave()) return;
    const rows = state.rows.filter(row => state.selected.get(row.id) === row.crew_id);
    if (!rows.length) return;
    const tomorrow = direction === 'tomorrow';
    let targetDay = state.date;
    if (tomorrow) {
      const next = new Date(state.date + 'T00:00:00Z');
      next.setUTCDate(next.getUTCDate() + 1);
      if (!Number.isFinite(next.getTime()) || next.getUTCFullYear() > 9999) {
        status('Для выбранной даты следующий день недоступен.', true); return;
      }
      targetDay = next.toISOString().slice(0, 10);
    }
    const payload = {date: targetDay, worker_ids: rows.map(row => row.id)};
    busy(true); status(tomorrow ? 'Проверка расстановки на завтра…' : 'Проверка расстановки предыдущего дня…');
    let plan;
    try { plan = await api('/api/staffing/transfer-selected/preview', {method: 'POST', body: JSON.stringify(payload)}); }
    catch (error) { status(error.message, true); return; }
    finally { busy(false); }
    const message = el('p', {role: 'status'});
    const close = () => { if (state.busy) return; state.transferEditor = null; dialog.close(); dialog.remove(); };
    const cancel = el('button', {type: 'button', className: 'secondary-button', onclick: close}, 'Отмена');
    const apply = el('button', {type: 'button', className: 'primary-button', disabled: !plan.ready}, 'Перенести (' + plan.ready + ')');
    const visible = new Set(visibleRows().map(row => row.id));
    const hidden = rows.filter(row => !visible.has(row.id)).length;
    const dialog = el('dialog', {className: 'staffing-work-dialog staffing-transfer-dialog', 'aria-labelledby': 'staffing-transfer-title'},
      el('h2', {id: 'staffing-transfer-title'}, tomorrow ? 'Перенести на завтра' : 'Перенос расстановки'),
      el('p', {}, plan.source_date.split('-').reverse().join('.') + ' → ' + plan.date.split('-').reverse().join('.')),
      el('p', {}, 'Будут перенесены подобъекты, смены, статусы явки и выполняемые работы. Исходная расстановка сохранится. Сотрудники, у которых на целевую дату уже есть данные, будут пропущены.'),
      el('p', {}, 'Готовы к переносу: ' + plan.ready + ' чел. Пропущены: ' + plan.skipped + ' чел.' +
        (hidden ? ' Среди выбранных скрыты фильтром: ' + hidden + ' чел.' : '')),
      el('ul', {className: 'staffing-transfer-list'}, ...plan.rows.map(row => el('li', {},
        el('strong', {}, row.full_name + (row.personnel_no ? ' · ' + row.personnel_no : '')),
        el('span', {}, row.ready ? row.assignments.map(item => (item.shift === '1 смена' ? 'День' : 'Ночь') + ': ' + item.object + ' / ' + item.subobject).join('; ') : 'Пропуск: ' + row.reason)))),
      message, el('div', {className: 'staffing-work-actions'}, cancel, apply));
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    apply.addEventListener('click', async () => {
      if (state.busy) return;
      busy(true); apply.disabled = true; cancel.disabled = true;
      let saved = false;
      try {
        const result = await api('/api/staffing/transfer-selected', {method: 'POST',
          body: JSON.stringify({...payload, expected_token: plan.expected_token})});
        saved = true; state.transferEditor = null; dialog.close(); dialog.remove();
        await load({skipInheritance: true});
        status((tomorrow ? 'Перенесено на ' + plan.date.split('-').reverse().join('.') + ': ' : 'Перенесено с предыдущего дня: ') +
          result.copied + ' чел. Пропущено: ' + result.skipped + ' чел.');
      } catch (error) {
        if (saved) status('Перенос сохранён. Не удалось обновить таблицу: ' + error.message, true);
        else { message.textContent = error.message; message.classList.add('error-text'); }
      } finally { busy(false); apply.disabled = !plan.ready; cancel.disabled = false; }
    });
    state.transferEditor = dialog; document.body.append(dialog); dialog.showModal();
  }
  function workControl(crew, row = null) {
    if (readOnly) return el('span', {className: 'staffing-work-text'}, row?.performed_work || 'Не указаны');
    const rows = row ? [row] : selectedRows(crew, true);
    return el('div', {className: 'staffing-work-cell'},
      row ? el('span', {className: 'staffing-work-text'}, row.performed_work || 'Не указаны') : null,
      el('button', {className: 'text-button', disabled: !rows.length || rows.some(r => !r.active || !r.employee_shift),
        'aria-label': row ? 'Выполняемые работы: ' + row.full_name : 'Выполняемые работы выбранных: ' + crew.name,
        onclick: () => openWorkEditor(rows)}, row ? 'Изменить' : 'Внести работы (' + rows.length + ')'));
  }
  async function openWorkEditor(targets) {
    if (!canLeave() || !targets.length) return;
    const ids = new Set(targets.map(row => row.id));
    busy(true);
    try {
      const crewIds = new Set(targets.map(row => row.crew_id));
      for (const crew of state.crews.filter(item => crewIds.has(item.id))) await loadCrew(crew);
    } catch (error) { status(error.message, true); return; }
    finally { busy(false); }
    const rows = state.rows.filter(row => ids.has(row.id));
    if (rows.length !== ids.size || rows.some(row => !row.active || !row.employee_shift)) {
      status('Сотрудник отключён или его смена не определена. Обновите расстановку.', true); return;
    }
    const day = state.date;
    const snapshot = {date: day, worker_ids: rows.map(row => row.id),
      expected_tokens: Object.fromEntries(rows.map(row => [row.id, row.performed_work_token])),
      expected_day_tokens: Object.fromEntries(rows.map(row => [row.id, row.day_token])), ...batchSnapshot(rows)};
    const common = rows.every(row => row.performed_work === rows[0].performed_work);
    const textarea = el('textarea', {id: 'staffing-work-description', rows: 7, maxLength: 2000,
      value: common ? rows[0].performed_work || '' : '', placeholder: 'Например: монтаж опалубки, бетонирование фундамента'});
    const message = el('p', {className: 'staffing-work-message', role: 'status'});
    const close = () => { if (state.busy) return; state.workEditor = null; dialog.close(); dialog.remove(); };
    const cancel = el('button', {type: 'button', className: 'secondary-button', onclick: close}, 'Отмена');
    const save = el('button', {type: 'button', className: 'primary-button', disabled: true}, 'Сохранить работы');
    textarea.addEventListener('input', () => { save.disabled = false; });
    const clear = el('button', {type: 'button', className: 'text-button', onclick: () => {
      if (state.busy) return; textarea.value = ''; save.disabled = false;
      message.textContent = 'При сохранении работы будут очищены у выбранных сотрудников.';
    }}, 'Очистить работы');
    const visible = new Set(visibleRows().map(row => row.id));
    const hidden = rows.filter(row => !visible.has(row.id)).length;
    const shiftLabel = rows.every(row => row.employee_shift === rows[0].employee_shift) ?
      (rows[0].employee_shift === '1 смена' ? 'День' : 'Ночь') : 'День и ночь — в смене каждого сотрудника';
    const dialog = el('dialog', {className: 'staffing-work-dialog', 'aria-labelledby': 'staffing-work-title'},
      el('h2', {id: 'staffing-work-title'}, 'Выполняемые работы'),
      el('p', {}, day.split('-').reverse().join('.') + ' · ' + shiftLabel + ' · Выбрано: ' + rows.length + ' чел.' +
        (hidden ? ' Скрыты фильтром: ' + hidden + '.' : '')),
      el('p', {}, 'Один текст будет сохранён всем выбранным сотрудникам на эту дату и их смену.'),
      !common ? el('p', {}, 'Сейчас у сотрудников разные работы. Введённый текст заменит их.') : null,
      el('label', {htmlFor: textarea.id}, 'Описание работ (до 2000 символов)'), textarea, message,
      el('div', {className: 'staffing-work-actions'}, clear, cancel, save));
    dialog.addEventListener('cancel', event => { event.preventDefault(); if (!state.busy) message.textContent = 'Нажмите «Отмена», чтобы закрыть форму без сохранения.'; });
    save.addEventListener('click', async () => {
      if (state.busy) return;
      busy(true); save.disabled = true; cancel.disabled = true; clear.disabled = true; textarea.disabled = true;
      try {
        const result = await api('/api/staffing/performed-work', {method: 'PUT', body: JSON.stringify({...snapshot, description: textarea.value})});
        const updated = new Map(result.rows.map(row => [row.id, row]));
        rows.forEach(row => Object.assign(row, updated.get(row.id)));
        state.workEditor = null; dialog.close(); dialog.remove(); render();
        status('Выполняемые работы сохранены: ' + rows.length + ' чел.');
      } catch (error) { message.textContent = error.message; message.classList.add('error-text'); }
      finally { busy(false); save.disabled = false; cancel.disabled = false; clear.disabled = false; textarea.disabled = false; }
    });
    state.workEditor = dialog; document.body.append(dialog); dialog.showModal(); textarea.focus();
  }
  function openCrewCreator(rows) {
    if (!window.crewCreator || !canLeave()) return;
    if (rows.some(row => !row.active)) { status('В составе есть отключённый сотрудник. Обновите выбор.', true); return; }
    const frozen = rows.map(row => ({...row}));
    state.createCrewEditor = true;
    window.crewCreator.open({rows: frozen, snapshot: batchSnapshot(frozen),
      onClosed: () => { state.createCrewEditor = false; },
      onSaved: async result => {
        frozen.forEach(row => state.selected.delete(row.id));
        try {
          await load({skipInheritance: true});
          status('Создана бригада «' + result.name + '»: ' + result.member_count + ' чел.' +
            (result.member_count ? '' : ' Она доступна в списке бригад для перевода сотрудников.'));
        } catch (_) { status('Бригада создана, но таблицу не удалось обновить. Нажмите «Обновить».', true); }
      }});
  }
  $('staffing-create-crew')?.addEventListener('click', () => openCrewCreator(
    state.rows.filter(row => state.selected.get(row.id) === row.crew_id)));
  function groupCrewControl(crew) {
    const rows = selectedRows(crew);
    const select = el('select', {disabled: !rows.length || rows.some(row => !row.active),
      'aria-label': 'Бригада выбранных: ' + crew.name,
      title: 'Новая бригада отмеченных сотрудников. Назначения на даты и индивидуальные ответственные сохраняются.'},
      el('option', {value: '', disabled: true, selected: true}, rows.length ? 'Выберите новую бригаду' : 'Выберите сотрудников'),
      ...(window.crewCreator ? [el('option', {value: 'create'}, '+ Создать новую…')] : []),
      ...state.crewOptions.map(item => el('option', {value: String(item.id)}, item.name + ' · ' + item.owner_name)));
    const apply = el('button', {className: 'text-button', disabled: true,
      'aria-label': 'Применить бригаду выбранным: ' + crew.name}, 'Задать (' + rows.length + ')');
    select.addEventListener('change', () => {
      if (select.value === 'create') { select.value = ''; apply.disabled = true; openCrewCreator(rows); return; }
      apply.disabled = select.disabled || !select.value || rows.every(row => row.crew_id === Number(select.value));
    });
    apply.addEventListener('click', async () => {
      if (!canLeave()) return;
      const target = state.crewOptions.find(item => item.id === Number(select.value));
      if (!target || !rows.length) return;
      const snapshot = batchSnapshot(rows);
      busy(true); status('Изменение бригады…');
      let saved = false;
      try {
        const result = await api('/api/staffing/groups/crew', {method: 'PUT', body: JSON.stringify({
          crew_id: target.id, target_token: target.target_token, worker_ids: rows.map(row => row.id), ...snapshot})});
        saved = true;
        rows.forEach(row => state.selected.delete(row.id));
        await load({skipInheritance: true});
        status('Бригада изменена: ' + result.changed + ' чел. → ' + result.crew_name + '. Назначения на даты сохранены.');
      } catch (error) {
        status(saved ? 'Бригада изменена, но таблицу не удалось обновить. Нажмите «Обновить».' :
          error.message + ' Обновите расстановку перед повторным переводом.', true);
      } finally { busy(false); }
    });
    return el('div', {className: 'staffing-contractor-control staffing-bulk-crew'}, select, apply);
  }
  function groupContractorControl(crew) {
    const rows = selectedRows(crew);
    const common = rows.length && rows.every(row => row.contractor_id === rows[0].contractor_id) ? rows[0].contractor_id : null;
    const select = el('select', {disabled: !rows.length || rows.some(row => !row.active),
      'aria-label': 'Подрядчик выбранных: ' + crew.name,
      title: 'Подрядчик отмеченных сотрудников. Изменение сохраняется для всех дат.'},
      el('option', {value: '', disabled: true}, rows.length ? 'Выберите подрядчика' : 'Выберите сотрудников'),
      ...state.contractors.filter(item => item.active || item.id === common).map(item =>
        el('option', {value: String(item.id), disabled: !item.active}, item.name + (item.active ? '' : ' (отключён)'))));
    select.value = common ? String(common) : '';
    const apply = el('button', {className: 'text-button', disabled: true,
      'aria-label': 'Применить подрядчика выбранным: ' + crew.name}, 'Задать (' + rows.length + ')');
    select.addEventListener('change', () => { apply.disabled = select.disabled || !select.value; });
    apply.addEventListener('click', async () => {
      if (!canLeave()) return;
      busy(true); status('Сохранение подрядчика…');
      try {
        const result = await api('/api/staffing/groups/contractor', {method: 'PUT', body: JSON.stringify({
          contractor_id: Number(select.value), worker_ids: rows.map(row => row.id),
          expected_tokens: Object.fromEntries(rows.map(row => [row.id, row.contractor_token])), ...batchSnapshot(rows)})});
        const updated = new Map(result.rows.map(row => [row.id, row]));
        rows.forEach(row => Object.assign(row, updated.get(row.id)));
        if (state.calendarFilter) await load(); else render();
        status('Подрядчик сохранён: ' + rows.length + ' чел.');
      } catch (error) {
        status(error.message + ' Обновите расстановку перед повторным изменением.', true);
      } finally { busy(false); }
    });
    return el('div', {className: 'staffing-contractor-control'}, select, apply);
  }
  function categoryControl(row) {
    const select = el('select', {'aria-label': 'Категория ГДЛР: ' + row.full_name,
      title: 'Категория из справочника. Сохраняется автоматически для всех дат и при повторном импорте.',
      disabled: !row.active || (!['admin', 'super_admin'].includes(root.dataset.role) && row.owner_user_id !== Number(root.dataset.userId))},
      el('option', {value: '', disabled: true}, row.category || 'Выберите категорию'),
      ...state.categories.filter(item => item.active || item.id === row.category_id).map(item =>
        el('option', {value: String(item.id), disabled: !item.active}, item.name + (item.active ? '' : ' (отключена)'))));
    select.value = row.category_id ? String(row.category_id) : '';
    select.addEventListener('change', async () => {
      const previous = row.category_id ? String(row.category_id) : '';
      if (!canLeave()) { select.value = previous; return; }
      const category = state.categories.find(item => String(item.id) === select.value);
      if (!category) { select.value = previous; return; }
      busy(true); status('Сохранение категории ГДЛР…');
      try {
        await api('/api/staffing/workers/' + row.id + '/category', {method: 'PUT', body: JSON.stringify({
          category_id: category.id, category_token: category.edit_token,
          expected_token: row.category_binding_token, expected_crew_id: row.crew_id})});
        await load({skipInheritance: true});
        status('Категория ГДЛР сохранена: ' + category.name + '.');
      } catch (error) {
        select.value = previous;
        status(error.message + ' Обновите расстановку перед повторным изменением.', true);
      } finally { busy(false); }
    });
    return select;
  }
  function employerControl(row) {
    if (readOnly) return row.employer || 'Не указан';
    return el('div', {className: 'staffing-work-cell'},
      el('span', {className: 'staffing-work-text'}, row.employer || 'Не указан'),
      el('button', {type: 'button', className: 'text-button', disabled: !row.active,
        'aria-label': 'Работодатель: ' + row.full_name,
        title: 'Изменить постоянную организацию-работодателя',
        onclick: () => openEmployerEditor([row])}, 'Сменить'));
  }
  async function openEmployerEditor(targets) {
    if (!canLeave() || !targets.length) return;
    const ids = new Set(targets.map(row => row.id));
    busy(true);
    try {
      const crewIds = new Set(targets.map(row => row.crew_id));
      for (const crew of state.crews.filter(item => crewIds.has(item.id))) await loadCrew(crew);
    } catch (error) { status(error.message, true); return; }
    finally { busy(false); }
    const rows = state.rows.filter(row => ids.has(row.id));
    if (rows.length !== ids.size || rows.some(row => !row.active || !row.employer_token)) {
      status('Состав сотрудников изменился. Обновите расстановку.', true); return;
    }
    const snapshot = {worker_ids: rows.map(row => row.id), ...batchSnapshot(rows),
      expected_tokens: Object.fromEntries(rows.map(row => [row.id, row.employer_token]))};
    const common = rows.every(row => row.employer === rows[0].employer);
    const input = el('input', {id: 'staffing-employer-name', type: 'text', maxLength: 200,
      value: common ? rows[0].employer || '' : '',
      placeholder: 'Выберите или введите организацию'});
    input.setAttribute('list', 'staffing-employer-suggestions');
    input.style.cssText = 'width:100%;max-width:100%;box-sizing:border-box';
    const suggestions = el('datalist', {id: 'staffing-employer-suggestions'},
      ...[...new Set(state.rows.map(row => row.employer).filter(Boolean))].sort((a,b) => a.localeCompare(b, 'ru'))
        .map(name => el('option', {value: name})));
    const message = el('p', {role: 'status'});
    const close = () => { if (state.busy) return; state.employerEditor = null; dialog.close(); dialog.remove(); };
    const cancel = el('button', {type: 'button', className: 'secondary-button', onclick: close}, 'Отмена');
    const save = el('button', {type: 'button', className: 'primary-button', disabled: !input.value.trim()}, 'Сохранить работодателя');
    input.addEventListener('input', () => { save.disabled = !input.value.trim(); });
    const visible = new Set(visibleRows().map(row => row.id));
    const hidden = rows.filter(row => !visible.has(row.id)).length;
    const dialog = el('dialog', {className: 'staffing-work-dialog', 'aria-labelledby': 'staffing-employer-title'},
      el('h2', {id: 'staffing-employer-title'}, 'Организация-работодатель'),
      el('p', {}, rows.length === 1 ? rows[0].full_name : 'Выбрано: ' + rows.length + ' чел.' +
        (hidden ? ' Скрыты фильтром: ' + hidden + '.' : '')),
      el('p', {}, 'Постоянная привязка в карточке сотрудника. Сохраняется при повторном импорте. Уже сохранённые назначения останутся с прежним работодателем.'),
      !common ? el('p', {}, 'Сейчас у сотрудников разные работодатели. Новое значение будет назначено всем выбранным.') : null,
      el('label', {htmlFor: input.id}, 'Организация-работодатель'), input, suggestions, message,
      el('div', {className: 'staffing-work-actions'}, cancel, save));
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    save.addEventListener('click', async () => {
      if (state.busy || !input.value.trim()) return;
      busy(true); save.disabled = true; cancel.disabled = true; input.disabled = true;
      let saved = false;
      try {
        await api('/api/staffing/groups/employer', {method: 'PUT', body: JSON.stringify({...snapshot, employer: input.value})});
        saved = true; state.employerEditor = null; dialog.close(); dialog.remove();
        await load({skipInheritance: true});
        status('Работодатель сохранён постоянно: ' + rows.length + ' чел.');
      } catch (error) {
        if (saved) status('Работодатель сохранён. Не удалось обновить таблицу: ' + error.message, true);
        else { message.textContent = error.message; message.classList.add('error-text'); }
      } finally { busy(false); save.disabled = !input.value.trim(); cancel.disabled = false; input.disabled = false; }
    });
    state.employerEditor = dialog; document.body.append(dialog); dialog.showModal(); input.focus();
  }
  function contractorControl(row) {
    if (readOnly) return row.contractor || 'Не указан';
    const select = el('select', {'aria-label': 'Подрядчик: ' + row.full_name,
      title: 'Подрядчик сотрудника. Изменение сохраняется для всех дат.',
      disabled: !row.active || (!['admin', 'super_admin'].includes(root.dataset.role) && !row.crew_id)},
      el('option', {value: '', disabled: true}, row.contractor || 'Выберите подрядчика'),
      ...state.contractors.filter(item => item.active || item.id === row.contractor_id).map(item =>
        el('option', {value: String(item.id), disabled: !item.active}, item.name + (item.active ? '' : ' (отключён)'))));
    select.value = row.contractor_id ? String(row.contractor_id) : '';
    select.addEventListener('change', async () => {
      const previous = row.contractor_id ? String(row.contractor_id) : '';
      if (!canLeave()) { select.value = previous; return; }
      busy(true); status('Сохранение подрядчика…');
      try {
        const result = await api('/api/staffing/workers/' + row.id + '/contractor', {
          method: 'PUT', body: JSON.stringify({contractor_id: Number(select.value),
            expected_token: row.contractor_token, expected_crew_id: row.crew_id})});
        Object.assign(row, result);
        if (state.calendarFilter) await load(); else if (state.groupMode === 'hierarchy') render(); else paintCrewPlace(displayedGroup(row));
        status('Подрядчик сотрудника сохранён.');
      } catch (error) {
        select.value = previous;
        status(error.message + ' Обновите расстановку перед повторным изменением.', true);
      } finally { busy(false); }
    });
    return select;
  }
  function scrollEditor(id) { $(id).focus({preventScroll: true}); $(id).scrollIntoView({block: 'start', behavior: 'smooth'}); }
  function attendanceControl(crew, row = null) {
    if (readOnly) return row?.attendance_status || '—';
    const rows = row ? [row] : selectedRows(crew);
    const common = rows.length && rows.every(r => r.attendance_status === rows[0].attendance_status) ? rows[0].attendance_status : '';
    const label = row ? row.full_name : crew.name;
    const select = el('select', {className: 'staffing-attendance-select', disabled: !rows.length || rows.some(r => !r.active),
      'aria-label': (row ? 'Статус: ' : 'Статус выбранных: ') + label},
      el('option', {value: '', disabled: true}, 'Разные'),
      ...['Явка', 'Вых', 'Без сод', 'Больн', 'МО'].map(value => el('option', {value}, value)));
    select.value = common;
    const save = async () => {
      if (!canLeave()) { select.value = common; return; }
      busy(true);
      try {
        await api('/api/staffing/status', {method: 'PUT', body: JSON.stringify({date: state.date,
          status: select.value, worker_ids: rows.map(r => r.id),
          expected_tokens: Object.fromEntries(rows.map(r => [r.id, r.attendance_token])),
          expected_group_tokens: Object.fromEntries(rows.map(r => [r.id, r.group_token]))})});
        await load(); status('Статус сохранён: ' + rows.length + ' чел.');
      } catch (error) {
        try { await load(); } catch (_) { /* The original save error remains visible. */ }
        status(error.message, true);
      } finally { busy(false); }
    };
    if (row) { select.addEventListener('change', save); return select; }
    const apply = el('button', {className: 'text-button', disabled: select.disabled || !common,
      'aria-label': 'Применить статус выбранным: ' + label, onclick: save}, 'Задать');
    select.addEventListener('change', () => { apply.disabled = select.disabled || !select.value; });
    return el('div', {className: 'staffing-attendance-control'}, select, apply);
  }
  function shiftControl(crew, row = null) {
    if (readOnly) return row?.employee_shift === '1 смена' ? 'День' : row?.employee_shift === '2 смена' ? 'Ночь' : 'Не указана';
    const rows = row ? [row] : selectedRows(crew, true);
    const common = rows.length && rows.every(r => r.employee_shift === rows[0].employee_shift) ? rows[0].employee_shift : '';
    const select = el('select', {className: row ? 'staffing-row-shift' : 'staffing-crew-shift',
      disabled: !rows.length || rows.some(r => r.locked), 'aria-label': row ? 'Смена: ' + row.full_name : 'Смена выбранных: ' + crew.name},
      el('option', {value: '', disabled: true}, row ? 'Выбрать' : 'Разные / нет'),
      el('option', {value: '1 смена'}, 'День'), el('option', {value: '2 смена'}, 'Ночь'));
    select.value = common || '';
    if (row) {
      select.addEventListener('change', () => {
        if (!canLeave()) { select.value = row.employee_shift || ''; return; }
        saveShifts(crew, rows, select.value);
      });
      return select;
    }
    const apply = el('button', {className: 'text-button', disabled: select.disabled || !select.value,
      title: 'Применить смену только отмеченным: ' + rows.length + ' чел.',
      'aria-label': 'Применить смену выбранным: ' + crew.name,
      onclick: () => { if (canLeave()) saveShifts(crew, rows, select.value); }}, 'Задать');
    select.addEventListener('change', () => { apply.disabled = !select.value; });
    return el('div', {className: 'staffing-shift-control'}, select, apply);
  }
  async function saveShifts(crew, rows, shift) {
    busy(true);
    try {
      await api(crew.itr || crew.id == null ? '/api/staffing/groups/shifts' : '/api/staffing/crews/' + crew.id + '/shifts', {method: 'PUT', body: JSON.stringify({
        date: state.date, shift, worker_ids: rows.map(row => row.id),
        expected_tokens: Object.fromEntries(rows.map(row => [row.id, row.day_token])), ...(crew.itr || crew.id == null ? batchSnapshot(rows) : {})})});
      await load(); status('Смена сохранена: ' + rows.length + ' чел. Места работы сохранены.');
    } catch (error) {
      try { await load(); } catch (_) { /* Keep the load error visible with the original failure. */ }
      status(error.message, true);
    } finally { busy(false); }
  }
  function responsibleButton(crew, row, field) {
    if (readOnly) return row?.[field + '_name'] || '—';
    const label = field === 'linear_itr' ? 'Линейный ИТР' : 'Бригадир';
    const supportsCrewless = field === 'linear_itr';
    const targets = row ? (supportsCrewless && row.crew_id == null ? [row] : null) : selectedRows(crew, supportsCrewless);
    const corrected = !!row && row[field + '_override'] != null;
    const value = row ? row[field + '_name'] : targets.length && targets.every(item => item[field + '_name'] === targets[0][field + '_name']) ? targets[0][field + '_name'] : '';
    return el('button', {className: 'staffing-cell-button' + (corrected ? ' corrected' : ''),
      disabled: (!crew.id && !supportsCrewless) || (!row && !targets.length), onclick: () => openDetails(crew, row, field, targets),
      'aria-label': label + (row ? ': ' + row.full_name : ' для выбранных: ' + crew.name),
      title: (value || 'Заполнить') + (row ? ' · Корректировка сотрудника' : ' · Только выбранным сотрудникам')},
      value || 'Заполнить', corrected ? el('small', {}, 'Исправлено в строке') : null);
  }
  function openDetails(crew, row = null, field = 'brigadier', targets = null) {
    if (!canLeave()) return;
    if (targets && !targets.length) return;
    state.details = {crew, row, field, targets, picks: {}, snapshot: targets ? batchSnapshot(targets) : null,
      expected: targets ? Object.fromEntries(targets.map(item => [item.id, item.row_token])) : null};
    $('staffing-details').hidden = false;
    $('staffing-details-title').textContent = targets ? (field === 'linear_itr' ? 'Линейный ИТР' : 'Бригадир') + ': выбрано ' + targets.length + ' чел. · ' + crew.name : row ? (field === 'linear_itr' ? 'Линейный ИТР: ' : 'Бригадир: ') + row.full_name : 'Вся бригада: ' + crew.name;
    $('staffing-details-note').textContent = targets ? 'Изменение только отмеченных сотрудников этой группы. «Как у бригады» вернёт каждому значение его бригады.' : row ? 'Изменение только этой строки. «Как у бригады» возвращает общее значение.' : 'ФИО ИТР и бригадира заполнятся для всей бригады. Индивидуальные корректировки обоих полей сохранятся.';
    $('staffing-itr-label').hidden = !!(row || targets) && field !== 'linear_itr';
    $('staffing-brigadier-label').hidden = !!(row || targets) && field !== 'brigadier';
    $('staffing-itr-field').hidden = $('staffing-itr-label').hidden;
    $('staffing-brigadier-field').hidden = $('staffing-brigadier-label').hidden;
    for (const [key, inputId] of [['linear_itr', 'staffing-itr'], ['brigadier', 'staffing-brigadier']]) {
      const source = row || targets?.[0];
      const effectiveId = item => item[key + '_override'] != null ? item[key + '_person_id'] : item['crew_' + key + '_person_id'];
      const shared = !targets || targets.every(item => item[key + '_name'] === source[key + '_name'] && effectiveId(item) === effectiveId(source));
      $(inputId).value = shared ? (source ? source[key + '_name'] : crew[key]) : '';
      const personId = shared ? (source ? effectiveId(source) : crew[key + '_person_id']) : null;
      state.details.picks[key] = {id: personId || null, dirty: false};
      $(inputId + '-manual').checked = false;
      $(inputId + '-results').replaceChildren();
      $(inputId + '-message').textContent = personId ? 'Выбран сотрудник из списка. Привязка сохранится ' + (targets ? 'для выбранных сотрудников.' : row ? 'для этой строки.' : 'за бригадой.') : 'Поиск по листу «Явка» и действующим сотрудникам аутстаффа. Выберите человека из списка.';
    }
    const onlyCrewless = targets?.every(item => item.crew_id == null);
    if (targets?.some(item => item.crew_id == null)) $('staffing-details-note').textContent =
      'Изменение только отмеченных сотрудников. Бригада не создаётся. Сброс корректировки вернёт значение бригады, а для сотрудников вне бригады очистит поле.';
    $('staffing-details-inherit').textContent = onlyCrewless ? 'Очистить ИТР' : 'Как у бригады';
    $('staffing-details-inherit').hidden = !(row || targets);
    scrollEditor('staffing-details');
  }
  async function saveDetails(inherit = false) {
    const edit = state.details;
    if (!edit || state.busy) return;
    const fields = edit.row || edit.targets ? [edit.field] : ['linear_itr', 'brigadier'];
    if (!inherit) for (const field of fields) {
      const inputId = field === 'linear_itr' ? 'staffing-itr' : 'staffing-brigadier';
      if (edit.picks[field].dirty && $(inputId).value.trim() && !edit.picks[field].id && !$(inputId + '-manual').checked) {
        $(inputId).focus();
        $(inputId + '-message').textContent = 'Выберите сотрудника из результатов поиска или включите «Вручную».';
        return;
      }
    }
    busy(true); status('Сохранение ответственных…');
    try {
      if (edit.targets) await api(edit.crew.itr || edit.crew.id == null ? '/api/staffing/groups/responsible' : '/api/staffing/crews/' + edit.crew.id + '/workers/responsible', {
        method: 'PUT', body: JSON.stringify({field: edit.field, worker_ids: edit.targets.map(item => item.id),
          value: inherit ? null : $(edit.field === 'linear_itr' ? 'staffing-itr' : 'staffing-brigadier').value,
          [edit.field + '_person_id']: inherit ? null : edit.picks[edit.field].id, expected_tokens: edit.expected,
          ...(edit.crew.itr || edit.crew.id == null ? edit.snapshot : {})})});
      else if (edit.row) await api('/api/staffing/crews/' + edit.crew.id + '/workers/' + edit.row.id + '/' + (edit.field === 'linear_itr' ? 'linear-itr' : 'brigadier'), {
        method: 'PUT', body: JSON.stringify({[edit.field + '_override']: inherit ? null : $(edit.field === 'linear_itr' ? 'staffing-itr' : 'staffing-brigadier').value,
          [edit.field + '_person_id']: inherit ? null : edit.picks[edit.field].id, expected_token: edit.row.row_token})});
      else await api('/api/staffing/crews/' + edit.crew.id + '/details', {method: 'PUT', body: JSON.stringify({
        linear_itr: $('staffing-itr').value, brigadier: $('staffing-brigadier').value,
        linear_itr_person_id: edit.picks.linear_itr.id, brigadier_person_id: edit.picks.brigadier.id, expected_token: edit.crew.details_token})});
      state.details = null; $('staffing-details').hidden = true; await load(); status('Ответственные сохранены.');
    } catch (error) { status(error.message, true); }
    finally { busy(false); }
  }
  $('staffing-category').addEventListener('change', () => {
    if (!canLeave()) { MF.set($('staffing-category'), state.category); return; }
    state.category = MF.get($('staffing-category'));
    if (!state.calendarFilter) preferences.set({category: state.category});
    render();
  });
  $('staffing-author').addEventListener('change', () => {
    if (!canLeave()) { MF.set($('staffing-author'), state.author); return; }
    state.author = MF.get($('staffing-author'));
    if (!state.calendarFilter) preferences.set({author: state.author});
    render();
  });
  $('staffing-department').addEventListener('change', () => {
    if (!canLeave()) { MF.set($('staffing-department'), state.department); return; }
    state.department = MF.get($('staffing-department'));
    if (!state.calendarFilter) preferences.set({department: state.department});
    render();
  });
  function updateHierarchyLabel() {
    const option = $('staffing-grouping').querySelector('option[value="hierarchy"]');
    option.textContent = hierarchy.describe(state.groupLevels);
    $('staffing-grouping').title = option.textContent + ' → сотрудники';
  }
  $('staffing-hierarchy-settings').addEventListener('click', () => {
    if (!canLeave()) return;
    hierarchy.openEditor(state.groupLevels, levels => {
      state.groupLevels = levels; state.groupMode = 'hierarchy';
      preferences.set({groupMode: 'hierarchy', groupLevels: levels});
      $('staffing-grouping').value = 'hierarchy'; updateHierarchyLabel();
      rebuildItrGroups(); state.collapsed = new Set(displayGroups().map(group => group.id));
      render(); status('Иерархия: ' + hierarchy.describe(levels) + '.');
    }, $('staffing-hierarchy-settings'));
  });
  $('staffing-grouping').addEventListener('change', () => {
    if (!canLeave()) { $('staffing-grouping').value = state.groupMode; return; }
    state.groupMode = $('staffing-grouping').value;
    preferences.set({groupMode: state.groupMode});
    rebuildItrGroups();
    state.collapsed = new Set(displayGroups().map(group => group.id));
    render();
    status(state.groupMode === 'hierarchy' ? 'Иерархия: ' + hierarchy.describe(state.groupLevels) + '.' : state.groupMode === 'itr' ? 'Группы по фактическому линейному ИТР с учётом корректировок сотрудников. Раскройте группу и отметьте сотрудников для массового назначения.' : 'Группы по бригадам.');
  });
  $('staffing-unassigned').addEventListener('change', () => {
    if (!canLeave()) { $('staffing-unassigned').checked = state.unassigned; return; }
    state.unassigned = $('staffing-unassigned').checked;
    if (!state.calendarFilter) preferences.set({unassigned: state.unassigned});
    render();
  });
  function searchTable() {
    if (!canLeave()) { $('staffing-search').value = state.search; $('staffing-regex').checked = state.regexMode; return; }
    const query = $('staffing-search').value;
    let expression = null;
    try {
      if ($('staffing-regex').checked && query) expression = new RegExp(SearchRegex.source(query), 'iu');
    } catch (_) {
      $('staffing-search-error').hidden = false;
      $('staffing-search-error').textContent = 'Некорректное регулярное выражение. Исправьте шаблон; таблица пока показывает предыдущий результат.';
      return;
    }
    $('staffing-search-error').hidden = true;
    state.search = query; state.regex = expression; state.regexMode = $('staffing-regex').checked;
    if (!state.calendarFilter) preferences.set({search: state.search, regexMode: state.regexMode}, {debounce: true});
    $('staffing-search').placeholder = state.regexMode ? 'Например: №(814 815)_ сварщик' : 'Бригада, ФИО, табельный номер, должность';
    render();
  }
  $('staffing-search').addEventListener('input', searchTable);
  $('staffing-regex').addEventListener('change', searchTable);
  ['staffing-date'].forEach(id => $(id).addEventListener('change', () => {
    if (!canLeave()) { $('staffing-date').value = state.date; MF.set($('staffing-shift'), state.shift); return; }
    if (!$('staffing-date').value) { $('staffing-date').value = state.date; return; }
    if (!$('staffing-date').validity.valid) { $('staffing-date').value = state.date; return; }
    preferences.set({date: $('staffing-date').value});
    load().catch(() => {});
  }));
  async function resetFilters() {
    if (!canLeave()) return;
    const reload = !!state.calendarFilter;
    const defaults = {department: '', employer: '', contractor: '', category: '', author: '', unassigned: false,
      search: '', regexMode: false, shift: '', pps: ''};
    MF.set(ppsFilter, '');
    if ($('staffing-freshness')) defaults.freshness = '';
    Object.assign(state, defaults, {regex: null, calendarFilter: null});
    state.selected.clear();
    for (const [field, id] of [['department', 'staffing-department'], ['employer', 'staffing-employer'], ['contractor', 'staffing-contractor'], ['category', 'staffing-category'],
      ['author', 'staffing-author'], ['search', 'staffing-search'], ['shift', 'staffing-shift'], ['freshness', 'staffing-freshness']]) {
      if ($(id)) { if ($(id).multiple) MF.set($(id), defaults[field]); else $(id).value = defaults[field]; }
    }
    $('staffing-unassigned').checked = false; $('staffing-regex').checked = false;
    $('staffing-search').placeholder = 'Бригада, ФИО, табельный номер, должность';
    $('staffing-search-error').hidden = true;
    preferences.set({...defaults, date: $('staffing-date').value});
    try {
      if (reload) await load({skipInheritance: true});
      else render();
      status('Фильтры сброшены. Показаны все сотрудники на выбранную дату.');
    } catch (error) { status('Фильтры сброшены, но таблицу не удалось загрузить. Нажмите «Обновить».', true); }
  }
  $('staffing-refresh').before(el('button', {id: 'staffing-reset-filters', className: 'secondary-button',
    title: 'Снять поиск и фильтры на выбранную дату', onclick: resetFilters}, 'Сбросить фильтры'));
  $('staffing-refresh').addEventListener('click', () => {
    const unresolvedOnly = !state.busy && !state.details && !state.crewDrafts.size &&
      state.drafts.size && [...state.drafts.values()].every(draft => draft.uncertain);
    if (unresolvedOnly || canLeave()) load({refreshReference: true}).catch(() => {});
  });
  $('staffing-clear-selected').addEventListener('click', clearSelected);
  $('staffing-calendar-filter-clear').addEventListener('click', () => {
    if (!canLeave()) return;
    state.calendarFilter = null; state.selected.clear();
    load().catch(() => {});
  });
  $('staffing-shift').addEventListener('change', () => {
    if (!canLeave()) { MF.set($('staffing-shift'), state.shift); return; }
    state.shift = MF.get($('staffing-shift'));
    if (!state.calendarFilter) preferences.set({shift: state.shift});
    if (state.calendarFilter) load().catch(() => {});
    else render();
  });
  $('staffing-collapse').addEventListener('click', () => {
    if (!canLeave()) return;
    state.collapsed = new Set(displayGroups().map(crew => crew.id)); render();
  });
  $('staffing-expand').addEventListener('click', async () => {
    if (!canLeave()) return;
    const visible = new Set(pageRows(visibleRows()).flatMap(groupKeys));
    const crews = displayGroups().filter(crew => visible.has(crew.id));
    const request = state.request;
    busy(true); status('Загрузка состава бригад…');
    try {
      for (let i = 0; i < crews.length; i += 4) {
        const batch = crews.slice(i, i + 4);
        await Promise.all(batch.map(loadGroup));
        if (request !== state.request) return;
        batch.forEach(crew => state.collapsed.delete(crew.id));
      }
      status('Состав бригад загружен.');
    } catch (error) { status(error.message, true); }
    finally { if (request === state.request) { render(); busy(false); } }
  });
  $('staffing-details-cancel').addEventListener('click', () => { state.details = null; $('staffing-details').hidden = true; status('Редактирование отменено.'); });
  $('staffing-details-form').addEventListener('submit', event => { event.preventDefault(); saveDetails(); });
  $('staffing-details-inherit').addEventListener('click', () => saveDetails(true));
  for (const [field, inputId] of [['linear_itr', 'staffing-itr'], ['brigadier', 'staffing-brigadier']]) {
    const input = $(inputId);
    const regex = el('input', {type: 'checkbox', id: inputId + '-regex', checked: true});
    const manual = el('input', {type: 'checkbox', id: inputId + '-manual'});
    const message = el('p', {id: inputId + '-message', role: 'status'});
    const results = el('div', {id: inputId + '-results', className: 'staffing-person-results'});
    $(inputId + '-field').append(el('div', {className: 'staffing-person-tools'},
      el('label', {className: 'check-label'}, regex, 'Regex'), el('label', {className: 'check-label'}, manual, 'Вручную')), message, results);
    let limit = 30;
    function searchPeople() {
      if (!state.details) return;
      results.replaceChildren();
      if (manual.checked) { message.textContent = 'Ручное ФИО. Привязки к сотруднику не будет.'; return; }
      const query = input.value.trim();
      let expression;
      try { expression = regex.checked && query ? new RegExp(SearchRegex.source(query), 'iu') : null; }
      catch (_) { message.textContent = 'Ошибка Regex: исправьте регулярное выражение.'; return; }
      const found = (state.people || []).filter(person => {
        const values = [person.full_name, person.personnel_no, person.profession, person.department, person.source_crew, person.employer || ''];
        return expression ? values.some(value => expression.test(value)) : matches(values.join(' '), query);
      });
      message.textContent = found.length ? 'Найдено: ' + found.length + '. Выберите сотрудника.' : 'Совпадений нет. Измените поиск.';
      if (!state.people?.length) message.textContent = 'Справочник ФИО ещё не загружен. Обновите расстановку или импортируйте сотрудников.';
      results.append(...found.slice(0, limit).map(person => el('button', {type: 'button', className: 'staffing-person-option', onclick: () => {
        input.value = person.full_name;
        state.details.picks[field] = {id: person.id, dirty: true};
        results.replaceChildren();
        message.textContent = 'Выбран: ' + person.full_name + ' · таб. № ' + person.personnel_no + ' · ' + person.profession;
      }}, el('strong', {}, person.full_name), el('small', {}, (person.source_kind === 'outstaff' ? 'Аутстафф · ' + person.employer + ' · код ' : 'Таб. № ') + person.personnel_no + ' · ' + person.profession + ' · ' + person.department + ' · ' + person.source_crew))));
      if (found.length > limit) results.append(el('button', {type: 'button', className: 'text-button', onclick: () => { limit += 30; searchPeople(); }}, 'Показать ещё'));
    }
    input.addEventListener('input', () => {
      if (!state.details) return;
      state.details.picks[field] = {id: null, dirty: true}; limit = 30; searchPeople();
    });
    input.addEventListener('focus', () => { if (state.details && !state.details.picks[field].id) searchPeople(); });
    regex.addEventListener('change', () => { limit = 30; searchPeople(); });
    manual.addEventListener('change', () => {
      state.details.picks[field] = {id: null, dirty: true}; limit = 30; searchPeople();
    });
  }
  $('staffing-file')?.addEventListener('change', () => {
    state.preview = null; $('staffing-import-apply').hidden = true; $('staffing-import-report').replaceChildren();
  });
  function renderImportComparison(plan) {
    const report = $('staffing-import-report');
    const person = row => (row.full_name || '') + ' · таб. № ' + (row.personnel_no || 'нет') +
      (row.department ? ' · ' + row.department : '') + (row.crew_name ? ' · ' + row.crew_name : '');
    const list = (title, rows, render) => {
      const content = el('div', {className: 'import-comparison-list'});
      let shown = 0;
      const more = el('button', {type: 'button', className: 'text-button'}, 'Показать ещё');
      const append = () => {
        content.append(...rows.slice(shown, shown + 40).map(render)); shown += 40;
        more.hidden = shown >= rows.length;
      };
      more.addEventListener('click', append); append();
      report.append(el('details', {className: 'import-comparison-section'},
        el('summary', {}, title + ': ' + rows.length), content, more));
    };
    report.append(el('p', {className: 'transfer-notice'}, 'Категории ГДЛР существующих сотрудников и текущие бригады сохраняются. ' +
      'Отсутствующие в файле исключаются из актуального состава этой ППС; история назначений сохраняется. Ручные записи остаются.'));
    if (plan.already_imported) report.append(el('p', {}, 'Этот файл уже импортирован. Повторная загрузка не заменит состав более новым или старым списком.'));
    report.append(el('p', {className: 'import-comparison-totals'},
      'Добавятся: ' + plan.counts.added + ' · Убавятся: ' + plan.counts.missing + ' · Сопоставлены: ' + plan.counts.matched +
      ' · Ручные записи остаются: ' + plan.counts.manual_kept));
    if (plan.unresolved) report.append(el('p', {className: 'error-text'}, 'Итог предварительный. Требуют решения: ' + plan.unresolved + '.'));
    list('Добавятся в состав', plan.added, row => el('p', {}, person(row)));
    list('Убавятся из состава', plan.missing, row => el('p', {}, person(row)));
    list('Сопоставлены с системой', plan.matched, row => el('div', {className: 'import-comparison-person'},
      el('p', {}, 'В системе: ' + person(row.system)), el('p', {}, 'В Excel: ' + person(row.file)),
      el('p', {}, 'ГДЛР сохраняется: ' + (row.category_preserved || 'не назначена'))));
    list('Ручные записи, которых нет в Excel', plan.manual_kept, row => el('p', {}, person(row)));
    const reviews = el('div', {className: 'import-identity-reviews'});
    for (const review of plan.reviews) {
      const select = el('select', {'aria-label': 'Решение по строке ' + review.file.source_row},
        el('option', {value: ''}, 'Выберите решение'), ...review.options.map(o => el('option', {value: o.value}, o.label)));
      select.value = review.decision;
      select.addEventListener('change', () => {
        if (!state.preview) return;
        if (select.value) state.preview.decisions[review.key] = select.value;
        else delete state.preview.decisions[review.key];
        state.preview.ready = false; $('staffing-import-apply').hidden = true;
        $('staffing-preview').textContent = 'Пересчитать итог сверки';
        hint.textContent = 'Решения изменены. Нажмите «Пересчитать итог сверки», затем подтвердите итог.';
      });
      reviews.append(el('div', {className: 'import-comparison-person'},
        el('strong', {}, review.kind === 'manual' ? 'Возможная ручная запись' : 'Различается ФИО при одинаковом табельном номере'),
        el('p', {}, 'Excel, строка ' + review.file.source_row + ': ' + person(review.file)),
        ...review.candidates.map(row => el('p', {}, 'В системе: ' + person(row))),
        el('label', {}, 'Подтвердите соответствие', select)));
    }
    const hint = el('p', {className: 'table-note'}, plan.reviews.length ?
      'Совпадение имени — только подсказка. При связывании ручной записи ФИО и табельный номер берутся из Excel; её назначения и ГДЛР сохраняются.' : 'Проверьте списки перед подтверждением импорта.');
    report.append(reviews, hint);
  }
  $('staffing-preview')?.addEventListener('click', async () => {
    if (!canLeave()) return;
    if (!$('staffing-import-date').reportValidity()) return;
    const file = $('staffing-file').files[0];
    if (!file) { $('staffing-import-report').textContent = 'Выберите шаблон перевахтовки в формате XLSX.'; return; }
    const decisions = state.preview?.decisions || {};
    busy(true); state.preview = null; $('staffing-import-apply').hidden = true;
    $('staffing-import-report').textContent = 'Проверка файла…';
    try {
      const body = new FormData(); body.append('file', file); body.append('source_label', importPps.value);
      body.append('decisions', JSON.stringify(decisions));
      const result = await api('/api/staffing/import/preview', {method: 'POST', body});
      state.preview = {file, token: result.preview_token, sourceLabel: importPps.value, decisions,
        ready: !result.issues.length && !result.reconciliation.unresolved};
      const count = result.summary;
      $('staffing-import-report').replaceChildren(el('p', {}, 'К импорту: ' + count.selected + ' чел., бригад: ' + count.crews + '. Без номера бригады: ' + (count.without_crew || 0) + '.'),
        el('p', {}, 'Исключено: другие подразделения — ' + (count.department_excluded || 0) + '; другая квалификация — ' + (count.qualification_excluded || 0) + '; водители, машинисты и вспомогательные — ' + (count.category_excluded || 0) + '.'),
        ...result.issues.map(message => el('p', {className: 'error-text'}, message)));
      renderImportComparison(result.reconciliation);
      $('staffing-preview').textContent = 'Пересчитать итог сверки';
      $('staffing-import-apply').hidden = !state.preview.ready;
      $('staffing-import-apply').textContent = 'Подтвердить изменения и импортировать';
      if (count.skipped_removed) $('staffing-import-report').append(el('p', {}, 'Ранее удалённые сотрудники будут пропущены: ' + count.skipped_removed + ' чел.'));
    } catch (error) { $('staffing-import-report').textContent = error.message; }
    finally { busy(false); }
  });
  $('staffing-import-apply')?.addEventListener('click', async () => {
    if (!canLeave() || !state.preview?.ready) return;
    if (!$('staffing-import-date').reportValidity()) return;
    const importDate = $('staffing-import-date').value;
    busy(true);
    try {
      const body = new FormData(); body.append('file', state.preview.file); body.append('preview_token', state.preview.token);
      body.append('source_label', state.preview.sourceLabel);
      const result = await api('/api/staffing/import/apply', {method: 'POST', body});
      state.preview = null; $('staffing-import-apply').hidden = true;
      $('staffing-import-report').textContent = result.people_added ? 'Справочник ФИО дополнен: ' + result.people_added + ' чел. Расстановка сохранена.' : result.already_imported ? 'Этот файл уже импортирован.' : 'Импортировано ' + result.selected + ' чел. Резервная копия базы создана.';
      if (result.reconciliation) $('staffing-import-report').append(el('p', {},
        'Добавились: ' + result.reconciliation.added + ' · Убавились: ' + result.reconciliation.missing +
        ' · Сопоставлены: ' + result.reconciliation.matched + '. Категории ГДЛР сохранены.'));
      $('staffing-date').value = importDate;
      preferences.set({date: importDate});
      await load();
    } catch (error) { state.preview = null; $('staffing-import-apply').hidden = true; $('staffing-import-report').textContent = error.message; }
    finally { busy(false); }
  });
  window.addEventListener('beforeunload', event => {
    if (state.busy || state.details || state.drafts.size || state.crewDrafts.size) { event.preventDefault(); event.returnValue = ''; }
  });
  const columnSettings = window.staffingColumns.create({headers, canChange: canLeave, status});
  const transferMenus = [...document.querySelectorAll('.staffing-transfers > details')];
  function closeTransferMenus(focus = false) {
    transferMenus.forEach(menu => {
      if (!menu.open) return;
      menu.open = false;
      if (focus) menu.querySelector('summary').focus();
    });
  }
  transferMenus.forEach(menu => {
    menu.querySelector('summary').addEventListener('click', () => {
      if (menu.open) return;
      transferMenus.forEach(other => { if (other !== menu) other.open = false; });
      if (menu.id === 'staffing-import') $('staffing-import-date').value = $('staffing-date').value;

    });
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('.staffing-transfers, .multi-filter-dialog')) closeTransferMenus();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && transferMenus.some(menu => menu.open) && !state.busy) {
      event.preventDefault(); closeTransferMenus(true);
    }
  });
  $('staffing-clear-selected').before(el('button', {id: 'staffing-work-selected', className: 'secondary-button', disabled: true,
    onclick: () => openWorkEditor(state.rows.filter(row => state.selected.get(row.id) === row.crew_id))}, 'Выполняемые работы (0)'));
  $('staffing-work-selected').before(el('button', {id: 'staffing-transfer-selected', className: 'secondary-button', disabled: true,
    onclick: () => openSelectedTransfer()}, 'Перенести со вчера (0)'),
    el('button', {id: 'staffing-transfer-tomorrow', className: 'secondary-button', disabled: true,
      onclick: () => openSelectedTransfer('tomorrow')}, 'Перенести на завтра (0)'));
  $('staffing-work-selected').before(el('button', {id: 'staffing-category-selected', className: 'secondary-button', disabled: true,
    onclick: () => openSelectedCategory()}, 'Изменить ГДЛР (0)'));
  $('staffing-work-selected').before(el('button', {id: 'staffing-employer-selected', className: 'secondary-button', disabled: true,
    onclick: () => openEmployerEditor(state.rows.filter(row => state.selected.get(row.id) === row.crew_id))}, 'Работодатель (0)'));
  let historyState = {undo: null, redo: null}, historyRequest = 0;
  const historyButtons = {};
  for (const [direction, label] of [['undo', 'Отменить'], ['redo', 'Повторить']]) {
    const button = el('button', {id: 'staffing-' + direction, type: 'button', className: 'secondary-button', disabled: true,
      onclick: () => replayHistory(direction)}, label);
    historyButtons[direction] = button;
    $('staffing-refresh').before(button);
  }
  async function refreshHistory() {
    if (readOnly) return;
    const requestId = ++historyRequest;
    try {
      const next = await api('/api/staffing/history');
      if (requestId !== historyRequest) return;
      historyState = next;
      for (const [direction, button] of Object.entries(historyButtons)) {
        const action = historyState[direction];
        button.disabled = !action;
        button.title = action ? action.label + ' · ' + action.date.split('-').reverse().join('.') + ' · ' + action.workers + ' чел.' : 'Нет действий для ' + (direction === 'undo' ? 'отмены' : 'повторения');
        button.setAttribute('aria-description', button.title);
      }
    } catch (_) {
      if (requestId !== historyRequest) return;
      historyState = {undo: null, redo: null};
      for (const button of Object.values(historyButtons)) {
        button.disabled = true; button.title = 'Не удалось загрузить историю. Нажмите «Обновить».';
      }
    }
  }
  async function replayHistory(direction) {
    if (readOnly) return;
    if (!canLeave() || !historyState[direction]) return;
    const action = historyState[direction];
    busy(true);
    let applied = false;
    try {
      const result = await api('/api/staffing/history/' + direction, {method: 'POST', body: JSON.stringify({id: action.id, token: action.token})});
      applied = true;
      state.date = result.date; $('staffing-date').value = result.date; preferences.set({date: result.date});
      state.calendarFilter = null;
      await load({skipInheritance: true});
      status((direction === 'undo' ? 'Отменено: ' : 'Повторено: ') + result.label + ' · ' + result.workers + ' чел.');
    } catch (error) { status((applied ? 'Действие выполнено, но таблица не обновилась: ' : '') + error.message, true); }
    finally { busy(false); await refreshHistory(); }
  }
  document.addEventListener('keydown', event => {
    if (readOnly || !event.ctrlKey && !event.metaKey || event.altKey || event.repeat || !$('view-staffing').classList.contains('active')) return;
    if (event.target.closest('input, textarea, select, [contenteditable="true"]')) return;
    const direction = event.code === 'KeyZ' ? (event.shiftKey ? 'redo' : 'undo') : event.code === 'KeyY' ? 'redo' : null;
    if (!direction) return;
    event.preventDefault(); replayHistory(direction);
  });
  $('staffing-refresh').addEventListener('click', refreshHistory);
  if (readOnly) {
    for (const id of ['undo', 'redo', 'transfer-selected', 'transfer-tomorrow', 'category-selected', 'employer-selected', 'work-selected', 'clear-selected']) {
      $('staffing-' + id).hidden = true;
    }
  }
  refreshHistory();
  window.staffingScreen = {load, canLeave, openFromCalendar, mobileSelection, clearSelection: () => {
    if (!canLeave()) return;
    state.selected.clear(); render(); status('Выделение снято. Назначения сохранены.');
  }};
})();
