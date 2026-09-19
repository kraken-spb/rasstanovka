(() => {
  'use strict';
  const root = document.getElementById('view-pps');
  if (!root) return;
  const shell = document.querySelector('.app-shell'), edit = shell.dataset.role === 'super_admin';
  const $ = id => document.getElementById(id);
  const E = (tag, props = {}, ...children) => {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
      else if (key in el) el[key] = value;
      else el.setAttribute(key, value);
    }
    children.flat().forEach(child => el.append(child)); return el;
  };
  const state = {rows: [], drafts: new Map(), busy: false};
  function status(text, error = false) {
    $('pps-status').textContent = text; $('pps-status').classList.toggle('error-text', error);
  }
  function canLeave() {
    if (root.hidden) return true;
    if (state.busy || state.drafts.size || $('pps-create-name').value.trim()) {
      status(state.busy ? 'Дождитесь сохранения.' : 'Сохраните или отмените изменения.', true); return false;
    }
    return true;
  }
  async function api(path, options = {}) {
    if (options.method) window.catalogData.invalidate();
    const response = await fetch(path, {...options, cache: 'no-store', headers: {
      'Content-Type': 'application/json', 'X-CSRF-Token': shell.dataset.csrf}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось выполнить запрос.');
    return data;
  }
  function render(data) {
    state.rows = data.rows;
    $('pps-count').textContent = data.rows.length;
    $('pps-unbound').textContent = data.unbound_smu_count;
    $('pps-empty').hidden = data.rows.length > 0;
    $('pps-list').replaceChildren(...data.rows.map(row => {
      const draft = state.drafts.get(row.id) || {name: row.name, active: !!row.active, expected_token: row.edit_token};
      const name = E('input', {value: draft.name, maxLength: 200, 'aria-label': 'Название ' + row.name});
      const active = E('input', {type: 'checkbox', checked: draft.active, 'aria-label': 'Доступность ' + row.name});
      const save = E('button', {type: 'button', className: 'primary-button', disabled: !state.drafts.has(row.id),
        onclick: () => change('/api/pps/' + row.id, 'PATCH', state.drafts.get(row.id), row.id)}, 'Сохранить');
      const cancel = E('button', {type: 'button', className: 'text-button', hidden: !state.drafts.has(row.id),
        onclick: () => {state.drafts.delete(row.id); render(data); status('Изменение отменено.');}}, 'Отмена');
      const changed = () => {
        if (name.value === row.name && active.checked === !!row.active) state.drafts.delete(row.id);
        else state.drafts.set(row.id, {...draft, name: name.value, active: active.checked});
        save.disabled = !state.drafts.has(row.id); cancel.hidden = save.disabled;
      };
      name.addEventListener('input', changed); active.addEventListener('change', changed);
      const links = E('div', {className: 'pps-members'}, E('strong', {}, 'СМУ: ' + row.smu.length),
        row.smu.length ? E('ul', {}, ...row.smu.map(s => E('li', {}, s.name + (s.active ? '' : ' (отключён)')))) :
          E('span', {}, 'Участки пока не привязаны'), E('small', {}, 'Сотрудников в участках: ' + row.employee_count));
      if (row.divisions?.length) links.append(E('strong', {}, 'Подразделения: '+row.divisions.length), E('ul', {}, ...row.divisions.map(d => E('li', {}, d.name + (d.active ? '' : ' (отключено)')))));
      return E('article', {className: 'pps-row'},
        edit ? E('label', {}, 'Название ППС', name) : E('strong', {}, row.name), links,
        edit ? E('label', {className: 'check-label'}, active, 'Доступен') : E('span', {}, row.active ? 'Доступен' : 'Отключён'),
        ...(edit ? [E('div', {className: 'pps-actions'}, save, cancel,
          E('button', {type: 'button', className: 'text-button error-text', disabled: !!row.smu.length || !!row.divisions?.length,
            title: row.smu.length ? 'Сначала отвяжите СМУ' : 'Удалить ППС', onclick: () => {
              if (!canLeave()) return;
              if (confirm('Удалить «' + row.name + '» из справочника?')) change('/api/pps/' + row.id, 'DELETE', {expected_token: row.edit_token}, row.id);
            }}, 'Удалить'))] : []));
    }));
  }
  async function load(refresh = false) {
    state.busy = true; root.inert = true;
    try {render(await window.catalogData.get('/api/pps', () => api('/api/pps'), {refresh})); status('');}
    catch (error) {status(error.message, true);}
    finally {state.busy = false; root.inert = false;}
  }
  async function change(path, method, body, id) {
    if (!edit || state.busy) return;
    state.busy = true; root.inert = true; status('Сохранение…');
    try {
      await api(path, {method, body: JSON.stringify(body)});
      if (id) state.drafts.delete(id); else $('pps-create-name').value = '';
      render(await api('/api/pps')); status(method === 'DELETE' ? 'ППС удалён.' : 'ППС сохранён.');
    } catch (error) {status(error.message, true);}
    finally {state.busy = false; root.inert = false;}
  }
  $('pps-create-form').addEventListener('submit', event => {
    event.preventDefault(); change('/api/pps', 'POST', {name: $('pps-create-name').value});
  });
  $('pps-refresh').addEventListener('click', () => {if (canLeave()) load(true);});
  window.addEventListener('beforeunload', event => {if (!canLeave()) {event.preventDefault(); event.returnValue = '';}});
  window.ppsScreen = {load, canLeave};
})();
