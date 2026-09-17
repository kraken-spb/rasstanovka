(() => {
  'use strict';
  const root = document.querySelector('.app-shell'), $ = s => document.querySelector(s);
  if (!root || !$('#view-workforce')) return;
  const state = {reference: null, offset: 0, limit: 50, total: 0, request: 0, card: null, tab: 'profile', busy: false, dirty: false, queue:'', view:'board'};
  const E = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined) continue;
      if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key in node) node[key] = value;
      else node.setAttribute(key, value);
    }
    for (const child of children.flat()) if (child != null) node.append(child);
    return node;
  };
  async function api(path, options = {}) {
    const response = await fetch('/api/workforce/' + path, {...options, cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf}});
    return window.readApiResponse(response, 'Не удалось выполнить запрос.');
  }
  const displayDate = value => value ? new Date(value + 'T12:00:00').toLocaleDateString('ru-RU') : '—';
  const label = value => state.reference?.catalog.find(row => row.code === value)?.label || value || '—';
  const board = window.createWorkforceBoard({container: $('#wf-board-region'), api, E, displayDate, openCard, reload: load});
  function error(message, card = false) {
    const node = $(card ? '#wf-card-error' : '#wf-error'); node.textContent = message || ''; node.hidden = !message;
  }
  function selectOptions(node, rows, key, text) {
    const previous = node.value;
    node.replaceChildren(node.options[0], ...rows.filter(r => r.active !== false && r.active !== 0).map(row => E('option', {value: row[key]}, row[text])));
    node.value = previous;
  }
  async function reference() {
    if (state.reference) return;
    state.reference = await api('reference');
    selectOptions($('#wf-department'), state.reference.departments, 'name', 'name');
    selectOptions($('#wf-stage'), [...state.reference.catalog.filter(r => r.kind === 'stage'),
      {code:'unconfirmed',label:'Без подтверждённого состояния',active:true}], 'code', 'label');
    selectOptions($('#wf-employer'), state.reference.organizations, 'id', 'name');
    selectOptions($('#wf-category'), state.reference.categories, 'id', 'name');
  }
  async function load() {
    clearTimeout(timer);
    const seq = ++state.request;
    renderView();
    error(''); $('#wf-count').textContent = 'Загрузка…';
    try {
      await reference();
      if (seq !== state.request) return;
      const query = new URLSearchParams({date: $('#wf-date').value, q: $('#wf-search').value,
        regex: $('#wf-regex').checked ? '1' : '0', department: $('#wf-department').value,
        stage: $('#wf-stage').value, employer: $('#wf-employer').value, category: $('#wf-category').value,
        conflicts: $('#wf-conflicts').checked ? '1' : '0', queue:state.queue === 'lifecycle' ? '' : state.queue, offset: state.offset, limit: state.limit});
      if (state.view === 'board') {
        const data = await board.load(query, state.reference);
        if (seq !== state.request || !data) return;
        state.total = data.totals.total;renderStats(data.totals);
        $('#wf-count').textContent = `${state.total} сотрудников по фильтру · в каждом этапе первые 20`;
        return;
      }
      const data = await api('people?' + query);
      if (seq !== state.request) return;
      state.total = data.totals.total;
      renderStats(data.totals);
      $('#wf-count').textContent = data.rows.length ? `${state.offset + 1}–${state.offset + data.rows.length} из ${state.total}` : 'Сотрудники не найдены';
      $('#wf-prev').disabled = !state.offset; $('#wf-next').disabled = state.offset + state.limit >= state.total;
      $('#wf-rows').replaceChildren(...data.rows.map(row => {
        const cell = (name, ...children) => E('td', {'data-label': name}, ...children);
        return E('tr', {}, cell('Сотрудник', E('button', {type: 'button', className: 'wf-person-link', onclick: () => openCard(row.id)}, row.full_name),
          E('small', {}, row.personnel_no || 'Табельный номер не указан'), row.conflicts ? E('small', {}, `⚠ Замечаний: ${row.conflicts}`) : null),
        cell('Проект / СМУ', row.project || 'Проект не уточнён', E('small', {}, row.department || '—')),
        cell('Работодатель', row.employer || '—'), cell('Должность / ГДЛР', row.profession || '—', E('small', {}, row.category || 'ГДЛР не указан')),
        cell('Статус сотрудника', row.employment || 'Не уточнён'), cell('Состояние', E('span', {className: 'wf-tag ' + (row.stage_code || '').split('.')[1]}, row.stage || 'Не подтверждено'),
          row.effective_date ? E('small', {}, 'с ' + displayDate(row.effective_date)) : null),
        cell('Заезд / прогноз выезда', displayDate(row.arrival_date), E('small', {}, displayDate(row.forecast_departure_date)),
          row.movement ? E('small',{},`${row.movement.direction==='arrival'?'Заезд':'Выезд'}: ${displayDate(row.movement.planned_date)} · ${row.movement.basis||'основание не уточнено'}`) : null,
          row.rotation ? E('small',{},`${row.rotation.schedule} · МО до ${displayDate(row.rotation.leave_end_date)} · следующий заезд ${displayDate(row.rotation.next_arrival_date)}`) : null));
      }));
    } catch (err) { if (seq === state.request) { error(err.message); $('#wf-count').textContent = 'Не удалось загрузить список'; } }
  }
  function renderStats(totals) {
    $('#wf-stats').replaceChildren(...[['total','Всего по фильтру'],['onsite','Явка'],['pvp','В ПВП'],['inbound','Заезд'],['on_leave','Неявка'],['unconfirmed','Без подтверждения']].map(([key, text]) =>
      E('div', {className: 'wf-stat'}, E('span', {}, text), E('strong', {}, String(totals[key])))));
  }
  function renderView() {
    const isBoard = state.view === 'board';
    $('#wf-board-region').hidden = !isBoard;$('#wf-table-region').hidden = isBoard;$('#wf-table-pagination').hidden = isBoard;
    for (const [id, active] of [['board', isBoard], ['table', !isBoard]]) {
      $('#wf-view-' + id).setAttribute('aria-pressed', String(active));
      $('#wf-view-' + id).classList.toggle('active', active);
    }
    for (const button of $('#wf-workspaces').children) {
      const active = button.dataset.workspace === state.queue;
      button.classList.toggle('active', active);button.setAttribute('aria-pressed', String(active));
    }
  }
  const tabs = [['profile','Карточка'],['rotations','Вахты и графики'],['stages','Присутствие'],['movements','Поездки'],['pvp','ПВП'],['documents','Документы'],['checks','Оформление'],['history','История']];
  function canDiscard() { return !state.busy && (!state.dirty || window.confirm('В карточке есть несохранённые изменения. Закрыть их?')); }
  async function openCard(id, keep = false) {
    if (!keep && !canDiscard()) return;
    try {
      await reference();
      const card = await api('people/' + id);
      state.card = card; state.dirty = false;
      if (!keep) state.tab = 'profile';
      $('#wf-card-title').textContent = card.profile.full_name;
      $('#wf-card-subtitle').textContent = [card.profile.department, card.profile.employer, card.profile.personnel_no].filter(Boolean).join(' · ');
      renderCard(); if (!$('#wf-card').open) $('#wf-card').showModal();
    } catch (err) { error(err.message, $('#wf-card').open); }
  }
  function field(key, title, type = 'text', options = {}) { return {key, title, type, ...options}; }
  const profileFields = [field('full_name','ФИО','text',{required:true}), field('profession_code','Должность / профессия','catalog',{kind:'profession',searchable:true,legacy:'profession'}),
    field('employer_id','Организация-работодатель','organization'),
    field('citizenship_code','Гражданство','catalog',{kind:'citizenship'}), field('employment_code','Статус сотрудника','catalog',{kind:'employment'}),
    field('birth_date','Дата рождения','date'), field('phone','Телефон'), field('messenger','Мессенджер'), field('origin_code','Город отправления','catalog',{kind:'travelpoint',searchable:true,legacy:'origin_city'}),
    field('rotation_schedule_id','График вахты','schedule',{allowIncomplete:true}), field('arrival_date','Дата заезда','date'), field('forecast_departure_date','Прогноз окончания вахты','date'),
    field('leave_start_date','Начало межвахтового отпуска','date'), field('leave_end_date','Окончание межвахтового отпуска','date'), field('notes','Примечания','textarea')];
  const schemas = {
    stage: [field('stage_code','Состояние','catalog',{kind:'stage',required:true}),field('effective_date','Дата события','date',{required:true}),field('confirmed','Событие подтверждено','checkbox')],
    movement: [field('direction','Направление','catalog',{kind:'direction',unprefixed:true,required:true}),field('planned_date','Плановая дата','date'),
      field('actual_date','Фактическая дата','date'),field('destination_kind','Место назначения','catalog',{kind:'destination',unprefixed:true,required:true}),
      field('basis_code','Тип заезда/выезда','catalog',{kind:'basis'}),field('result_code','Статус заезда/выезда','catalog',{kind:'result'}),
      field('origin_code','Откуда','catalog',{kind:'travelpoint',searchable:true,legacy:'origin'}),field('destination_code','Куда','catalog',{kind:'travelpoint',searchable:true,legacy:'destination'}),field('travel_details','Билет / транспорт','textarea'),field('notes','Примечания','textarea')],
    document: [field('document_code','Документ','catalog',{kind:'document',required:true}),field('state_code','Состояние','catalog',{kind:'docstate'}),field('number','Номер'),
      field('issued_on','Дата выдачи','date'),field('expires_on','Действует до','date'),field('notes','Примечания','textarea')],
    pvp: [field('place_id','Место ПВП','place'),field('planned_arrival','План прибытия','date'),field('arrived_on','Фактически прибыл','date'),field('departed_on','Выбыл','date'),field('notes','Примечания','textarea')],
    check: [field('state_code','Состояние','catalog',{kind:'checkstate',required:true}),field('planned_date','Плановая дата','date'),field('completed_date','Дата завершения','date'),field('notes','Примечания','textarea')]
  };
  async function save(kind, values, existing, requestKey) {
    const id = state.card.profile.id;
    let path = `people/${id}/${kind}`, method = 'POST';
    if (kind === 'rotation') { path = `people/${id}/rotations`; }
    else if (kind === 'extend') { path = `people/${id}/rotations/${existing.id}/extend`; values.token = existing.edit_token; }
    else if (kind === 'finish_rotation') { path = `people/${id}/rotations/${existing.id}/finish`; values.token = existing.edit_token; }
    else if (kind === 'rotation_schedule') { path = 'rotation-schedules' + (existing ? '/' + existing.id : '');
      if (existing) {method = 'PATCH';values.token = existing.edit_token;} }
    else if (kind === 'reschedule') { path = `people/${id}/movements/${existing.id}/reschedule`; values.token = existing.edit_token; }
    else if (kind === 'profile') { method = 'PATCH'; values.token = state.card.profile.token; }
    else if (kind === 'check') { path = `people/${id}/checks/${existing.check_code}`; method = 'PUT'; values.token = existing.edit_token || 'new'; }
    else if (existing) { path += '/' + existing.id; method = 'PATCH'; values.token = existing.edit_token; }
    values.request_key = requestKey;
    state.busy = true; error('', true);
    $('#wf-card-content').inert = true;
    try {
      const result = await api(path, {method, body: JSON.stringify(values)});
      if (kind === 'rotation_schedule') {state.reference = null;await reference();}
      state.dirty = false; await openCard(id, true); await load();
      if (result.trip_plans_to_review?.length) error('Вахта продлена. Есть ранее оформленные поездки с другими датами — проверьте их на вкладке «Поездки».', true);
    } catch (err) { error(err.message, true); }
    finally { state.busy = false; $('#wf-card-content').inert = false; }
  }
  const isActive = row => row.active !== false && row.active !== 0;
  function referenceValues(spec, includeInactive = false) {
    let rows;
    if (spec.type === 'catalog') rows = state.reference.catalog.filter(row => row.kind === spec.kind).map(row => ({...row,
      value:spec.unprefixed ? row.code.slice(spec.kind.length + 1) : row.code, text:row.label}));
    else if (spec.type === 'place') rows = state.reference.places.map(row => ({...row,value:row.id,text:row.name}));
    else if (spec.type === 'organization') rows = state.reference.organizations.map(row => ({...row,value:row.id,text:row.name}));
    else if (spec.type === 'schedule') rows = state.reference.rotation_schedules.map(row => ({...row,value:row.id,
      text:row.name + (row.needs_review ? ' · параметры не уточнены' : '')}));
    else return spec.values.map(([value,text]) => [String(value),text]);
    return rows.filter(row => includeInactive || (isActive(row) && (spec.type !== 'schedule' || spec.allowIncomplete || !row.needs_review)))
      .map(row => [String(row.value),row.text]);
  }
  function referenceText(spec, value) {
    return referenceValues(spec,true).find(([code]) => code === String(value))?.[1] || value || '—';
  }
  let fieldId = 0;
  function searchableField(control, values, existing) {
    const {input,spec} = control, id = 'wf-reference-' + (++fieldId);
    const legacy = !existing?.[spec.key] && existing?.[spec.legacy] ? String(existing[spec.legacy]) : '';
    const search = E('input',{type:'search',placeholder:'Поиск по вариантам',autocomplete:'off','aria-label':'Поиск: ' + spec.title});
    const hint = E('small',{id:id + '-hint',className:'wf-reference-hint'});
    const count = E('small',{className:'wf-reference-count',role:'status','aria-live':'polite'});
    const clear = E('button',{type:'button',className:'secondary-button wf-reference-clear'},'Очистить');
    input.id = id;input.setAttribute('aria-describedby',hint.id);
    search.setAttribute('aria-controls',id);
    const normalize = value => value.toLocaleLowerCase('ru').replace(/ё/g,'е').trim();
    const indexed = values.map(([value,text]) => ({value,text,search:normalize(text)}));
    function renderOptions() {
      const selected = input.value, query = normalize(search.value), matched = indexed.filter(row => row.search.includes(query));
      const shown = indexed.filter(row => row.value === selected || row.search.includes(query));
      input.replaceChildren(E('option',{value:''},legacy && !control.forceClear ? 'Сохранить исходное значение' : 'Не выбрано'),
        ...shown.map(row => E('option',{value:row.value},row.text)));
      input.value = selected;
      count.textContent = query ? `Найдено: ${matched.length} из ${indexed.length}` : `Вариантов: ${indexed.length}`;
    }
    function updateHint() {
      hint.textContent = legacy ? control.forceClear ? 'Исходное значение будет очищено после сохранения.' : input.value ?
        'Исходное значение: ' + legacy : 'Не сопоставлено: ' + legacy : input.value ? referenceText(spec,input.value) : 'Выберите значение из справочника.';
      hint.classList.toggle('is-unmapped',!!legacy && !input.value && !control.forceClear);
      clear.textContent = legacy && control.forceClear ? 'Вернуть исходное' : 'Очистить';
      clear.disabled = !input.value && !legacy;
    }
    search.addEventListener('input',renderOptions);
    search.addEventListener('keydown',event => {if (event.key === 'Enter') {event.preventDefault();input.focus();}});
    input.addEventListener('change',() => {control.forceClear = !!legacy && !input.value;state.dirty = true;renderOptions();updateHint();});
    clear.addEventListener('click',() => {
      input.value = '';control.forceClear = legacy ? !control.forceClear : false;
      state.dirty = true;renderOptions();updateHint();
    });
    renderOptions();updateHint();
    return E('div',{className:'wf-reference-field'},E('label',{htmlFor:id},spec.title),
      E('div',{className:'wf-reference-picker'},search,input,hint,E('div',{className:'wf-reference-footer'},count,clear)));
  }
  function form(kind, fields, existing = null) {
    const formNode = E('form', {className:'wf-form'}), controls = {};
    let attempt = null;
    for (const spec of [...fields, field('reason','Основание изменения','textarea',{required:true})]) {
      let input, values;
      if (['catalog','select','place','schedule','organization'].includes(spec.type)) {
        values = referenceValues(spec);
        if (existing?.[spec.key] && !values.some(([value]) => value === String(existing[spec.key]))) {
          values.push([String(existing[spec.key]),referenceText(spec,existing[spec.key]) + ' · недоступно для новых назначений']);
        }
        input = E('select', {required:!!spec.required}, E('option',{value:''},'Выберите'), ...values.map(([value,text]) => E('option',{value},text)));
      } else input = E(spec.type === 'textarea' ? 'textarea' : 'input', {type:spec.type === 'textarea' ? undefined : spec.type, required:!!spec.required});
      input.name = spec.key;
      if (spec.type === 'checkbox') input.checked = !!existing?.[spec.key];
      else input.value = existing?.[spec.key] ?? '';
      if (kind === 'movement' && existing && spec.key === 'planned_date') {
        input.disabled = true; input.title = 'Изменение даты — через действие «Перенести поездку».';
      }
      input.addEventListener('input', () => {state.dirty = true;});
      const control = {input,spec,initial:spec.type === 'checkbox' ? input.checked : input.value,forceClear:false};
      controls[spec.key] = control;
      formNode.append(spec.searchable ? searchableField(control,values,existing) : E('label',{className:spec.type === 'textarea' ? 'wf-wide' : ''}, spec.title, input));
    }
    formNode.append(E('button',{type:'submit',className:'primary-button wf-wide'},existing ? 'Сохранить изменения' : 'Добавить запись'));
    formNode.addEventListener('submit', event => {
      event.preventDefault(); if (state.busy || !formNode.reportValidity()) return;
      const values = {};
      const patch = existing && !['extend','finish_rotation','reschedule','check','rotation_schedule'].includes(kind);
      for (const [key,{input,spec,initial,forceClear}] of Object.entries(controls)) if (!input.disabled) {
        const current = spec.type === 'checkbox' ? input.checked : input.value;
        if (patch && key !== 'reason' && current === initial && !forceClear) continue;
        if (spec.type === 'organization' && !input.value && !existing?.[key]) continue;
        values[key] = spec.type === 'checkbox' ? input.checked : spec.type === 'number' ? Number(input.value) : input.value;
      }
      const fingerprint = JSON.stringify(values);
      if (!attempt || attempt.fingerprint !== fingerprint) attempt = {fingerprint,key:crypto.randomUUID()};
      save(kind, values, existing, attempt.key);
    });
    if (kind === 'rotation' || kind === 'extend') {
      const preview = E('p',{className:'wf-wide',role:'status'});
      formNode.append(preview);
      const recalculate = () => {
        const schedule = kind === 'extend' ? existing.schedule_snapshot : state.reference.rotation_schedules.find(s => s.id === controls.schedule_id.input.value);
        const start = kind === 'extend' ? existing.start_date : controls.start_date.input.value;
        if (!schedule || !start || schedule.needs_review) {preview.textContent = 'Выберите уточнённый график и начало вахты для расчёта дат.';return;}
        const next = (day, n) => {const value = new Date(day + 'T12:00:00Z');value.setUTCDate(value.getUTCDate()+n);return value.toISOString().slice(0,10);};
        const end = kind === 'extend' ? controls.new_end_date.input.value : controls.planned_end_date.input.value || next(start,schedule.onsite_days-1);
        if (!end) return;
        const leaveEnd = next(end,schedule.leave_days), arrival = next(leaveEnd,schedule.travel_days);
        preview.textContent = `Окончание вахты: ${displayDate(end)} · Окончание МО: ${displayDate(leaveEnd)} · Следующий заезд по графику: ${displayDate(arrival)}. Оформленные билеты и заявки корректируются отдельно.`;
      };
      formNode.addEventListener('input',recalculate);recalculate();
    }
    return formNode;
  }
  function renderCard() {
    error('', true);
    const visibleTabs = state.card.private_details === false ? tabs.filter(([key]) => ['profile','rotations','stages','movements'].includes(key)) : tabs;
    $('#wf-card-tabs').replaceChildren(...visibleTabs.map(([key,text]) => E('button',{type:'button',className:state.tab === key ? 'active' : '',
      'aria-pressed':String(state.tab === key),onclick:() => {if (canDiscard()) {state.tab = key;state.dirty = false;renderCard();}}},text)));
    const content = $('#wf-card-content'); content.replaceChildren();
    const p = state.card.profile, perms = state.reference.permissions;
    if (state.tab === 'profile') {
      content.append(E('dl',{className:'wf-facts'}, ...[['Проект',p.project],['СМУ',p.department],['Работодатель',p.employer],['Должность',p.profession],['Категория ГДЛР',p.category],['Табельный №',p.personnel_no]].map(([key,value]) => E('div',{},E('dt',{},key),E('dd',{},value || '—')))));
      const editable = perms.profile && !(state.card.role === 'rotation' && p.employment_code !== 'employment.staff') && !(state.card.role === 'recruitment' && p.employment_code === 'employment.staff');
      const fields = state.card.role === 'recruitment' ? profileFields.filter(f => !['arrival_date','forecast_departure_date','leave_start_date','leave_end_date'].includes(f.key)) : profileFields;
      if (editable) content.append(form('profile',fields,p));
      else content.append(E('dl',{className:'wf-facts'},...profileFields.filter(f => f.key in p || (f.legacy && f.legacy in p)).map(f => E('div',{},E('dt',{},f.title),E('dd',{},
        f.type === 'catalog' ? p[f.key] ? referenceText(f,p[f.key]) : f.legacy && p[f.legacy] ? 'Не сопоставлено: ' + p[f.legacy] : '—' :
          f.type === 'schedule' ? p.rotation_schedule || '—' : f.type === 'organization' ? p.employer || '—' : p[f.key] || '—')))));
      for (const conflict of state.card.conflicts) content.append(E('article',{className:'wf-entry'},E('h3',{},'⚠ ' + conflict.field_name),E('p',{},conflict.description),E('small',{},conflict.resolution || 'Требует уточнения')));
      for (const source of state.card.sources) content.append(E('small',{className:'wf-entry'},`${source.filename} · ${source.sheet} · строка ${source.source_row}`,source.mapping_notes ? E('p',{},source.mapping_notes) : null));
      return;
    }
    if (state.tab === 'history') {
      if (!state.card.history.length) content.append(E('p',{},'Изменений в новом учёте пока нет. История расстановки сохранена в разделе «Логи».'));
      for (const row of state.card.history) content.append(E('article',{className:'wf-entry'},E('h3',{},row.actor_name + ' · ' + new Date(row.changed_at).toLocaleString('ru-RU')),E('p',{},row.entity_type + ' · ' + row.action),E('p',{},row.reason),
        row.action === 'extend' ? E('p',{},'Окончание вахты: ' + displayDate(row.before_json.planned_end_date) + ' → ' + displayDate(row.after_json.planned_end_date)) : null));
      return;
    }
    if (state.tab === 'rotations') {
      if (perms.movement) content.append(E('details',{className:'wf-entry'},E('summary',{},'Запланировать вахту'),
        form('rotation',[field('schedule_id','График вахтования','schedule',{required:true}),field('start_date','Начало вахты','date',{required:true}),field('planned_end_date','Окончание (пусто — по графику)','date'),field('notes','Примечания','textarea')])));
      for (const row of state.card.rotations) {
        const block = E('article',{className:'wf-entry'},E('h3',{},row.schedule_snapshot.name + ' · ' + displayDate(row.start_date) + ' — ' + displayDate(row.planned_end_date)),
          E('p',{},'Окончание МО: ' + displayDate(row.leave_end_date) + ' · Следующий заезд по графику: ' + displayDate(row.next_arrival_date)),E('p',{},row.notes));
        if (perms.movement && !row.cancelled && !row.actual_end_date) block.append(E('details',{},E('summary',{},'Продлить вахту'),form('extend',[field('new_end_date','Новая дата окончания','date',{required:true})],row)));
        if (perms.movement && !row.cancelled && !row.actual_end_date) block.append(E('details',{},E('summary',{},'Завершить или отменить'),
          form('finish_rotation',[field('action','Действие','select',{values:[['close','Завершить вахту'],['cancel','Отменить план']],required:true}),
            field('actual_end_date','Фактическое окончание (при завершении)','date')],row)));
        if (row.cancelled || row.actual_end_date) block.append(E('strong',{},row.cancelled ? 'План отменён' : 'Вахта завершена ' + displayDate(row.actual_end_date)));
        content.append(block);
      }
      if (!state.card.rotations.length) content.append(E('p',{},'Вахты ещё не внесены. Исходный график: ' + (p.rotation_schedule || 'не указан')));
      if (perms.catalog) {
        const scheduleFields = [field('name','Название графика','text',{required:true}),field('onsite_days','Дней на вахте','number',{required:true}),
          field('leave_days','Дней МО','number',{required:true}),field('travel_days','Дней после МО до заезда','number',{required:true})];
        const catalog = E('details',{className:'wf-entry'},E('summary',{},'Справочник графиков вахтования'));
        catalog.append(E('p',{},'Изменения справочника применяются к новым вахтам. Сохранённые планы и продления сохраняют свои параметры.'));
        for (const row of state.reference.rotation_schedules) catalog.append(E('details',{className:'wf-entry'},
          E('summary',{},row.name + (row.needs_review ? ' · параметры требуют уточнения' : '') + (!row.active ? ' · архив' : '')),
          form('rotation_schedule',[...scheduleFields,field('active','Действующий график','checkbox')],row)));
        catalog.append(E('details',{},E('summary',{},'Добавить график'),form('rotation_schedule',scheduleFields)));
        content.append(catalog);
      }
      return;
    }
    const kind = {stages:'stage',movements:'movement',pvp:'pvp',documents:'document',checks:'check'}[state.tab];
    if (kind === 'check') {
      for (const check of state.reference.catalog.filter(r => r.kind === 'check' && (isActive(r) || state.card.checks.some(row => row.check_code === r.code)))) {
        const saved = state.card.checks.find(r => r.check_code === check.code), row = saved || {check_code:check.code};
        const block = E('details',{className:'wf-entry'},E('summary',{},check.label + ' · ' + label(row.state_code) + (!isActive(check) ? ' · архив справочника' : '')));
        if (perms.check && isActive(check)) block.append(form('check',schemas.check,row));
        else if (saved) block.append(E('dl',{className:'wf-facts'},...[
          ['Плановая дата',displayDate(row.planned_date)],['Дата завершения',displayDate(row.completed_date)],['Примечания',row.notes || '—']
        ].map(([title,value]) => E('div',{},E('dt',{},title),E('dd',{},value)))));
        content.append(block);
      }
      return;
    }
    if (perms[kind]) content.append(E('details',{className:'wf-entry'},E('summary',{},'Добавить запись'),form(kind,schemas[kind])));
    if (!state.card[state.tab].length) content.append(E('p',{},'Записей пока нет.'));
    for (const row of state.card[state.tab]) {
      const title = kind === 'stage' ? `${label(row.stage_code)} · ${displayDate(row.effective_date)} · ${row.retracted ? 'ошибочное событие отозвано' : row.confirmed ? 'подтверждено' : 'не подтверждено'}` :
        kind === 'movement' ? `${row.direction === 'arrival' ? 'Заезд' : 'Выезд'} · ${displayDate(row.actual_date || row.planned_date)} · ${label(row.result_code)}` :
        kind === 'document' ? `${label(row.document_code)} · ${label(row.state_code)}` :
        `${state.reference.places.find(place => place.id === row.place_id)?.name || 'Место не уточнено'} · ${displayDate(row.arrived_on || row.planned_arrival)}`;
      const block = E('article',{className:'wf-entry'},E('h3',{},title),E('p',{},row.notes || row.reason || ''));
      if (kind === 'stage') block.append(E('small',{},row.actor_name || ''));
      else if (perms[kind]) {
        const superseded = kind === 'movement' && state.card.movements.some(next => next.rescheduled_from === row.id);
        if (!superseded) block.append(E('details',{},E('summary',{},'Корректировать'),form(kind,schemas[kind],row)));
        if (kind === 'movement' && !superseded && !row.actual_date && !['result.happened','result.cancelled'].includes(row.result_code)) {
          block.append(E('details',{},E('summary',{},'Перенести поездку'),form('reschedule',[field('planned_date','Новая плановая дата','date',{required:true})],row)));
        }
        if (superseded) block.append(E('small',{},'Архивный план. Сохранён после переноса.'));
        if (row.rescheduled_from) {
          const previous = state.card.movements.find(old => old.id === row.rescheduled_from);
          block.append(E('small',{},'Перенос с ' + (previous ? displayDate(previous.planned_date) : 'предыдущего плана')));
        }
      }
      content.append(block);
    }
  }
  let timer;
  for (const [value,title] of [['','Весь состав'],['lifecycle','Перемещения'],['plans','Плановые поездки'],['pvp','ПВП и оформление'],['rotations','Графики вахтования']]) {
    $('#wf-workspaces').append(E('button',{type:'button','data-workspace':value,'aria-pressed':String(!value),className:!value?'active':'',onclick:()=>{
      if (!board.canLeave()) return;
      state.queue=value;state.offset=0;
      if (value === 'lifecycle') {state.view='board';$('#wf-stage').value='';filterLabel();}
      else if (['plans','pvp','rotations'].includes(value)) state.view='table';
      load();
    }},title));
  }
  for (const view of ['board','table']) $('#wf-view-' + view).addEventListener('click', () => {
    if (!board.canLeave()) return;
    state.view=view;state.offset=0;
    if (view === 'board' && !['','lifecycle'].includes(state.queue)) state.queue='';
    load();
  });
  $('#wf-filter-panel').open = !window.matchMedia('(max-width: 760px)').matches;
  function filterLabel() {
    const count = ['search','department','stage','employer','category'].filter(id => $('#wf-' + id).value).length + Number($('#wf-conflicts').checked) + Number($('#wf-regex').checked);
    $('#wf-filter-summary').textContent = 'Фильтры' + (count ? ' · выбрано ' + count : '');
  }
  $('#wf-filter-panel').addEventListener('input',filterLabel);
  $('#wf-search').addEventListener('input', () => {clearTimeout(timer);timer = setTimeout(() => {state.offset = 0;load();},250);});
  for (const id of ['date','regex','department','stage','employer','category','conflicts']) $('#wf-' + id).addEventListener('change',() => {state.offset = 0;load();});
  $('#wf-refresh').addEventListener('click',() => {state.reference = null;load();});
  $('#wf-prev').addEventListener('click',() => {state.offset = Math.max(0,state.offset-state.limit);load();});
  $('#wf-next').addEventListener('click',() => {state.offset += state.limit;load();});
  $('#wf-card-close').addEventListener('click',() => {if (canDiscard()) {state.dirty = false;$('#wf-card').close();}});
  $('#wf-card').addEventListener('cancel',event => {if (!canDiscard()) event.preventDefault();else state.dirty = false;});
  window.addEventListener('beforeunload',event => {if (state.dirty || state.busy || board.busy()) {event.preventDefault();event.returnValue = '';}});
  window.workforceScreen = {load,invalidate:() => {state.reference=null;},canLeave:() => board.canLeave() && (!$('#wf-card').open || canDiscard())};
  const paths = {workforce:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M16 3a4 4 0 0 1 0 8 M22 21v-2a4 4 0 0 0-3-3.87 M13 7a4 4 0 1 1-8 0a4 4 0 0 1 8 0',
    staffing:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',dashboard:'M4 3h16v18H4z M8 7h8 M8 11h8 M8 15h4',
    analytics:'M3 3v18h18 M7 16v-5 M12 16V7 M17 16V4',catalogs:'M4 4h16v5H4z M4 15h16v5H4z',
    accounts:'M12 12a4 4 0 1 0 0-8a4 4 0 0 0 0 8 M4 21v-2a6 6 0 0 1 6-5h4a6 6 0 0 1 6 5v2',
    verification:'M4 3h16v18H4z M8 12l3 3 6-7',outstaff:'M3 7h18v14H3z M8 7V3h8v4',employees:'M4 4h16v16H4z M8 8h8 M8 12h8 M8 16h4',
    logs:'M4 4h16v16H4z M8 8h8 M8 12h8 M8 16h8',plan:'M4 5h16v16H4z M4 10h16 M8 2v6 M16 2v6',backups:'M4 4h16v16H4z M7 4v7h10V4 M8 16h8'};
  for (const button of document.querySelectorAll('.desktop-nav button')) {
    const text = button.textContent.trim(); button.title = text; button.setAttribute('aria-label',text);
    const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
    for (const [key,value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.6','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'})) svg.setAttribute(key,value);
    const path = document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',paths[button.dataset.view] || 'M4 6h16 M4 12h16 M4 18h16');svg.append(path);
    button.replaceChildren(svg,E('span',{},text));
  }
  const storageKey = 'workforce-sidebar:' + root.dataset.userId;
  function expand(value) {document.body.classList.toggle('sidebar-expanded',value);$('#sidebar-toggle').setAttribute('aria-expanded',String(value));$('#sidebar-toggle').setAttribute('aria-label',value ? 'Свернуть меню' : 'Развернуть меню');}
  try {expand(localStorage.getItem(storageKey) === 'true');} catch (_) {expand(false);}
  $('#sidebar-toggle').addEventListener('click',() => {const value = !document.body.classList.contains('sidebar-expanded');expand(value);try {localStorage.setItem(storageKey,String(value));} catch (_) {}});
})();
