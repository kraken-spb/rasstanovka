(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('view-categories')) return;
  const root = document.querySelector('.app-shell');
  const canEdit = root.dataset.role === 'super_admin';
  const state = {rows: [], drafts: new Map(), createDraft: '', busy: false};
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
  const visible = () => $('view-categories').classList.contains('active');
  const hasDrafts = () => state.drafts.size || state.createDraft.length > 0;
  function status(message, isError = false) {
    const node = $('categories-status'); node.textContent = message; node.classList.toggle('error-text', isError);
  }
  function error(message = '') { $('categories-error').textContent = message; $('categories-error').hidden = !message; }
  async function api(url, options = {}) {
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
  async function refresh(force = false) {
    if (!force && !canLeave()) return;
    state.busy = true; $('view-categories').inert = true; error(); status('Загрузка справочника…');
    try {
      state.rows = (await api('/api/gdlr-categories')).rows;
      render(); status('');
    } catch (failure) { error(failure.message); }
    finally { state.busy = false; $('view-categories').inert = false; }
  }
  async function load() { await refresh(true); }
  function rowDraft(item) { return state.drafts.get(item.id) || {name: item.name, active: !!item.active, expected_token: item.edit_token}; }
  function render() {
    const count = state.rows.length;
    const bound = state.rows.reduce((total, item) => total + item.employee_count, 0);
    $('categories-count').textContent = count.toLocaleString('ru-RU');
    $('categories-bound').textContent = bound.toLocaleString('ru-RU');
    const create = $('category-create-name');
    if (create.value !== state.createDraft) create.value = state.createDraft;
    const list = $('category-catalog-list');
    list.replaceChildren(...state.rows.map(item => {
      if (!canEdit) return el('article', {className: 'category-catalog-row'},
        el('strong', {}, item.name), el('span', {}, item.active ? 'Доступна для выбора' : 'Отключена'),
        el('small', {}, 'Связано сотрудников: ' + item.employee_count));
      const draft = rowDraft(item);
      const name = el('input', {value: draft.name, maxLength: 200, required: true, 'aria-label': 'Название категории ' + item.name});
      const active = el('input', {type: 'checkbox', checked: draft.active, 'aria-label': 'Доступность категории ' + item.name});
      const save = el('button', {className: 'primary-button', disabled: !state.drafts.has(item.id), onclick: () => saveCategory(item)}, 'Сохранить');
      const cancel = el('button', {className: 'text-button', hidden: !state.drafts.has(item.id), onclick: () => {
        state.drafts.delete(item.id); render(); status('Изменение отменено. Обновите справочник, если нужно получить текущие данные.');
      }}, 'Отмена');
      const changed = () => {
        if (name.value === item.name && active.checked === !!item.active) state.drafts.delete(item.id);
        else state.drafts.set(item.id, {name: name.value, active: active.checked, expected_token: draft.expected_token});
        save.disabled = !state.drafts.has(item.id); cancel.hidden = save.disabled;
      };
      name.addEventListener('input', changed); active.addEventListener('change', changed);
      return el('article', {className: 'category-catalog-row'}, el('label', {}, 'Название', name),
        el('label', {className: 'check-label'}, active, 'Доступна для выбора'),
        el('small', {}, 'Связано сотрудников: ' + item.employee_count), el('div', {className: 'category-actions'}, save, cancel,
          el('button', {type: 'button', className: 'text-button error-text', 'aria-label': 'Удалить категорию ' + item.name,
            onclick: () => remove(item)}, 'Удалить')));
    }));
    $('categories-empty').hidden = count !== 0;
  }
  async function remove(item) {
    if (!canEdit || state.busy) return;
    if (hasDrafts()) { status('Сохраните или отмените изменения перед удалением.', true); return; }
    if (!confirm('Удалить категорию «' + item.name + '» из справочника? Это действие нельзя отменить.')) return;
    state.busy = true; $('view-categories').inert = true; error(); status('Удаление…');
    try {
      await api('/api/gdlr-categories/' + item.id, {method: 'DELETE', body: JSON.stringify({expected_token: item.edit_token})});
      state.rows = state.rows.filter(row => row.id !== item.id);
      render(); status('Категория удалена.');
    } catch (failure) { status('Удаление не выполнено.', true); error(failure.message); }
    finally { state.busy = false; $('view-categories').inert = false; }
  }
  async function saveCategory(item) {
    const draft = state.drafts.get(item.id);
    if (!canEdit || !draft || state.busy) return;
    state.busy = true; $('view-categories').inert = true; error(); status('Сохранение категории…');
    try {
      await api('/api/gdlr-categories/' + item.id, {method: 'PATCH', body: JSON.stringify(draft)});
      state.drafts.delete(item.id);
      state.rows = (await api('/api/gdlr-categories')).rows;
      render(); status('Категория сохранена.');
    } catch (failure) { error(failure.message + ' Изменение осталось в поле: отмените его и обновите справочник для явной сверки.'); }
    finally { state.busy = false; $('view-categories').inert = false; }
  }
  async function create(event) {
    event.preventDefault();
    const name = state.createDraft.trim();
    if (!canEdit || !name || state.busy) return;
    state.busy = true; $('view-categories').inert = true; error(); status('Добавление категории…');
    try {
      await api('/api/gdlr-categories', {method: 'POST', body: JSON.stringify({name})});
      state.createDraft = ''; state.rows = (await api('/api/gdlr-categories')).rows;
      render(); status('Категория добавлена.');
    } catch (failure) { error(failure.message + ' Название осталось в поле.'); }
    finally { state.busy = false; $('view-categories').inert = false; }
  }
  $('category-create-form').addEventListener('submit', create);
  $('category-create-name').addEventListener('input', event => { state.createDraft = event.target.value; });
  $('categories-refresh').addEventListener('click', () => refresh());
  window.addEventListener('beforeunload', event => {
    if (visible() && (state.busy || hasDrafts())) { event.preventDefault(); event.returnValue = ''; }
  });
  window.categoriesScreen = {load, canLeave};
})();
