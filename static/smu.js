(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('view-smu')) return;
  const root = document.querySelector('.app-shell');
  const canEdit = root.dataset.role === 'super_admin';
  const state = {rows: [], chiefOptions: [], ppsOptions: [], drafts: new Map(), createDraft: '', createChief: null, createPps: null, busy: false};
  const el = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    Object.entries(props).forEach(([key, value]) => {
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key in node) node[key] = value;
      else node.setAttribute(key, value);
    });
    children.flat().forEach(child => node.append(child));
    return node;
  };
  const visible = () => $('view-smu').classList.contains('active');
  const hasDrafts = () => state.drafts.size || state.createDraft.length > 0 || state.createChief !== null || state.createPps !== null;
  function setCatalog(data) { state.rows = data.rows; state.chiefOptions = data.chief_options || []; state.ppsOptions = data.pps_options || []; }
  function fillPps(select, value) {
    select.replaceChildren(el('option', {value: ''}, 'Без привязки к ППС'), ...state.ppsOptions
      .filter(p => p.active || p.id === value).map(p => el('option', {value: String(p.id), disabled: !p.active}, p.name + (p.active ? '' : ' (отключён)'))));
    select.value = value === null ? '' : String(value);
  }
  function fillChiefOptions(select, value, item = null) {
    const options = [el('option', {value: ''}, 'Не назначено'), ...state.chiefOptions.map(user =>
      el('option', {value: String(user.id)}, user.full_name + ' (' + user.username + ')'))];
    if (value !== null && !state.chiefOptions.some(user => user.id === value)) {
      const name = item?.site_chief_user_id === value ? item.site_chief_name : 'Выбранный пользователь';
      options.push(el('option', {value: String(value), disabled: true}, (name || 'Пользователь') + ' (отключён)'));
    }
    select.replaceChildren(...options);
    select.value = value === null ? '' : String(value);
  }
  function chiefSelect(value, item) {
    const select = el('select', {style: 'width:100%;min-width:0;max-width:100%'});
    fillChiefOptions(select, value, item);
    return select;
  }
  function status(message, isError = false) {
    const node = $('smu-status'); node.textContent = message; node.classList.toggle('error-text', isError);
  }
  function error(message = '') { $('smu-error').textContent = message; $('smu-error').hidden = !message; }
  async function api(url, options = {}) {
    if (options.method && options.method !== 'GET') window.catalogData.invalidate();
    const response = await fetch(url, { ...options, cache: 'no-store', headers: {
      'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf,
    }});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось выполнить запрос.');
    return data;
  }
  function canLeave() {
    if (!visible()) return true;
    if (state.busy || hasDrafts()) {
      status(state.busy ? 'Дождитесь сохранения.' : 'Сохраните или отмените изменения в справочнике.', true);
      return false;
    }
    return true;
  }
  async function refresh(force = false, reuse = false) {
    if (!force && !canLeave()) return;
    state.busy = true; $('view-smu').inert = true; error(); status('Загрузка справочника…');
    try {
      setCatalog(await window.catalogData.get('/api/smu', () => api('/api/smu'), {refresh: !reuse}));
      render(); status('');
    } catch (failure) { error(failure.message); }
    finally { state.busy = false; $('view-smu').inert = false; }
  }
  async function load() { await refresh(true, true); }
  function rowDraft(item) { return state.drafts.get(item.id) || {name: item.name, active: !!item.active,
    site_chief_user_id: item.site_chief_user_id ?? null, pps_id: item.pps_id ?? null, expected_token: item.edit_token}; }
  function render() {
    const count = state.rows.length;
    const bound = state.rows.reduce((total, item) => total + item.employee_count, 0);
    $('smu-count').textContent = count.toLocaleString('ru-RU');
    $('smu-bound').textContent = bound.toLocaleString('ru-RU');
    const create = $('smu-create-name');
    if (create.value !== state.createDraft) create.value = state.createDraft;
    fillChiefOptions($('smu-create-chief'), state.createChief);
    fillPps($('smu-create-pps'), state.createPps);
    const list = $('smu-catalog-list');
    list.replaceChildren(...state.rows.map(item => {
      if (!canEdit) return el('article', {className: 'category-catalog-row'},
        el('strong', {}, item.name), el('span', {}, item.active ? 'Доступно для выбора' : 'Отключено'),
        el('span', {}, 'ППС: ' + (item.pps_name || 'Не привязан')),
        el('span', {style: 'min-width:0;overflow-wrap:anywhere'}, 'Ответственное лицо: ' +
          (item.site_chief_name ? item.site_chief_name + (item.site_chief_active ? '' : ' (отключён)') : 'Не назначено')),
        el('small', {}, 'Связано сотрудников: ' + item.employee_count));
      const draft = rowDraft(item);
      const name = el('input', {value: draft.name, maxLength: 500, required: true, 'aria-label': 'Название СМУ ' + item.name});
      const active = el('input', {type: 'checkbox', checked: draft.active, 'aria-label': 'Доступность СМУ ' + item.name});
      const chief = chiefSelect(draft.site_chief_user_id, item);
      chief.setAttribute('aria-label', 'Ответственное лицо СМУ ' + item.name);
      const pps = el('select', {'aria-label': 'ППС для ' + item.name}); fillPps(pps, draft.pps_id);
      const save = el('button', {className: 'primary-button', disabled: !state.drafts.has(item.id), onclick: () => saveSmu(item)}, 'Сохранить');
      const cancel = el('button', {className: 'text-button', hidden: !state.drafts.has(item.id), onclick: () => {
        state.drafts.delete(item.id); render(); status('Изменение отменено. Обновите справочник, если нужно получить текущие данные.');
      }}, 'Отмена');
      const changed = () => {
        const chiefId = chief.value ? Number(chief.value) : null;
        const ppsId = pps.value ? Number(pps.value) : null;
        if (name.value === item.name && active.checked === !!item.active && chiefId === (item.site_chief_user_id ?? null) && ppsId === (item.pps_id ?? null)) state.drafts.delete(item.id);
        else state.drafts.set(item.id, {name: name.value, active: active.checked,
          site_chief_user_id: chiefId, pps_id: ppsId, expected_token: draft.expected_token});
        save.disabled = !state.drafts.has(item.id); cancel.hidden = save.disabled;
      };
      name.addEventListener('input', changed); active.addEventListener('change', changed); chief.addEventListener('change', changed);
      pps.addEventListener('change', changed);
      return el('article', {className: 'category-catalog-row smu-pps-row'}, el('label', {}, 'Название', name),
        el('label', {}, 'ППС', pps),
        el('label', {}, 'Ответственное лицо', chief),
        el('label', {className: 'check-label'}, active, 'Доступно для выбора'),
        el('small', {}, 'Связано сотрудников: ' + item.employee_count), el('div', {className: 'category-actions'}, save, cancel,
          el('button', {type: 'button', className: 'text-button error-text', 'aria-label': 'Удалить СМУ ' + item.name,
            onclick: () => remove(item)}, 'Удалить')));
    }));
    $('smu-empty').hidden = count !== 0;
  }
  async function remove(item) {
    if (!canEdit || state.busy) return;
    if (hasDrafts()) { status('Сохраните или отмените изменения перед удалением.', true); return; }
    if (!confirm('Удалить СМУ «' + item.name + '» из справочника? Это действие нельзя отменить.')) return;
    state.busy = true; $('view-smu').inert = true; error(); status('Удаление…');
    try {
      await api('/api/smu/' + item.id, {method: 'DELETE', body: JSON.stringify({expected_token: item.edit_token})});
      state.rows = state.rows.filter(row => row.id !== item.id);
      render(); status('СМУ удалено.');
    } catch (failure) { status('Удаление не выполнено.', true); error(failure.message); }
    finally { state.busy = false; $('view-smu').inert = false; }
  }
  async function saveSmu(item) {
    const draft = state.drafts.get(item.id);
    if (!canEdit || !draft || state.busy) return;
    state.busy = true; $('view-smu').inert = true; error(); status('Сохранение СМУ…');
    try {
      await api('/api/smu/' + item.id, {method: 'PATCH', body: JSON.stringify(draft)});
      state.drafts.delete(item.id);
      setCatalog(await api('/api/smu'));
      render(); status('СМУ сохранено.');
    } catch (failure) { error(failure.message + ' Изменение осталось в поле: отмените его и обновите справочник для явной сверки.'); }
    finally { state.busy = false; $('view-smu').inert = false; }
  }
  async function create(event) {
    event.preventDefault();
    const name = state.createDraft.trim();
    if (!canEdit || !name || state.busy) return;
    state.busy = true; $('view-smu').inert = true; error(); status('Добавление СМУ…');
    try {
      await api('/api/smu', {method: 'POST', body: JSON.stringify({name, site_chief_user_id: state.createChief, pps_id: state.createPps})});
      state.createDraft = ''; state.createChief = null; state.createPps = null; setCatalog(await api('/api/smu'));
      render(); status('СМУ добавлено.');
    } catch (failure) { error(failure.message + ' Название осталось в поле.'); }
    finally { state.busy = false; $('view-smu').inert = false; }
  }
  const createChief = chiefSelect(null);
  createChief.id = 'smu-create-chief';
  createChief.addEventListener('change', () => { state.createChief = createChief.value ? Number(createChief.value) : null; });
  $('smu-create-form').insertBefore(el('label', {}, 'Ответственное лицо', createChief), $('smu-create-form').querySelector('button'));
  const createPps = el('select', {id: 'smu-create-pps', onchange: event => {state.createPps = event.target.value ? Number(event.target.value) : null;}});
  $('smu-create-form').insertBefore(el('label', {}, 'ППС', createPps), $('smu-create-form').querySelector('button'));
  $('smu-create-form').addEventListener('submit', create);
  $('smu-create-name').addEventListener('input', event => { state.createDraft = event.target.value; });
  $('smu-refresh').addEventListener('click', () => refresh());
  window.addEventListener('beforeunload', event => {
    if (visible() && (state.busy || hasDrafts())) { event.preventDefault(); event.returnValue = ''; }
  });
  window.smuScreen = {load, canLeave};
})();
