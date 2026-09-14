(() => {
  'use strict';
  const root = document.querySelector('.app-shell');
  if (!root || !['admin', 'super_admin'].includes(root.dataset.role)) return;
  const el = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key in node) node[key] = value;
      else node.setAttribute(key, value);
    }
    children.flat().forEach(child => node.append(child)); return node;
  };
  const option = (value, label) => el('option', {value}, label);
  async function api(url, options = {}) {
    const response = await fetch(url, {...options, cache: 'no-store', headers: {
      'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось добавить сотрудника.');
    return data;
  }
  function open({onSaved, onClosed}) {
    let saving = false, reference = null;
    const requestKey = crypto.randomUUID();
    const input = (id, maxLength, required = false) => el('input', {id: 'employee-new-' + id, maxLength, required, autocomplete: 'off'});
    const fullName = input('name', 200, true), number = input('number', 100, true);
    const internal = el('input', {id: 'employee-new-internal', type: 'checkbox'});
    internal.addEventListener('change', () => { number.disabled = internal.checked; number.required = !internal.checked; if (internal.checked) number.value = ''; });
    const profession = input('profession', 300), gsp = input('gsp', 300), employer = input('employer', 200);
    const department = el('select', {id: 'employee-new-department'}), category = el('select', {id: 'employee-new-category'});
    const contractor = el('select', {id: 'employee-new-contractor'}), crew = el('select', {id: 'employee-new-crew'});
    const qualification = el('select', {id: 'employee-new-qualification'}, ...['', 'Рабочие', 'ИТР', 'Другое'].map(value => option(value, value || 'Не указана')));
    const pps = el('select', {id: 'employee-new-pps'}, ...['', 'ППС15', 'ППС19'].map(value => option(value, value || 'Не указана')));
    const message = el('p', {role: 'status', className: 'save-status'}, 'Загрузка справочников…');
    const save = el('button', {type: 'submit', className: 'primary-button', disabled: true}, 'Добавить сотрудника');
    const close = () => { if (saving) return; dialog.close(); dialog.remove(); onClosed(); };
    const label = (title, control) => el('label', {}, title, control);
    const form = el('form', {className: 'stack-form'},
      label('ФИО *', fullName),
      el('div', {className: 'employee-create-grid'}, label('Табельный номер *', number), label('Квалификация', qualification)),
      el('label', {className: 'employee-create-check'}, internal, 'Табельного номера нет — присвоить внутренний код MAN'),
      el('div', {className: 'employee-create-grid'}, label('Должность', profession), label('Профессия ГСП', gsp),
        label('Категория ГДЛР', category), label('Компания-подрядчик', contractor),
        label('Организация-работодатель', employer), label('СМУ', department), label('ППС', pps), label('Бригада', crew)),
      el('p', {className: 'table-note'}, 'Бригаду можно не выбирать: сотрудник появится в списке «Без бригады», где ему доступны смена и место работы.'),
      message, el('div', {className: 'employee-create-actions'}, el('button', {type: 'button', className: 'secondary-button', onclick: close}, 'Отмена'), save));
    const dialog = el('dialog', {id: 'employee-create-dialog', className: 'app-dialog employee-create-dialog', 'aria-labelledby': 'employee-create-title'},
      el('h2', {id: 'employee-create-title'}, 'Добавить сотрудника'), form);
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (saving || !reference || !form.reportValidity()) return;
      const chosenCategory = reference.categories.find(item => String(item.id) === category.value);
      const chosenContractor = reference.contractors.find(item => String(item.id) === contractor.value);
      const payload = {request_key: requestKey, full_name: fullName.value, personnel_no: internal.checked ? '' : number.value,
        internal_number: internal.checked, profession: profession.value, gsp_profession: gsp.value, employer: employer.value,
        department: department.value, pps: pps.value, qualification: qualification.value,
        crew_id: crew.value ? Number(crew.value) : null,
        category_id: chosenCategory?.id || null, category_token: chosenCategory?.edit_token,
        contractor_id: chosenContractor?.id || null, contractor_token: chosenContractor?.edit_token};
      saving = true; form.inert = true; save.disabled = true;
      message.classList.remove('error-text'); message.textContent = 'Добавление сотрудника…';
      let result;
      try { result = await api('/api/employees', {method: 'POST', body: JSON.stringify(payload)}); }
      catch (error) { message.textContent = error.message; message.classList.add('error-text'); }
      finally { saving = false; form.inert = false; save.disabled = false; }
      if (result) { close(); await onSaved(result); }
    });
    document.body.append(dialog); dialog.showModal();
    Promise.all([api('/api/employees/create-options'), api('/api/staffing/crew-options')]).then(([data, crews]) => {
      if (!dialog.isConnected) return;
      reference = data;
      department.replaceChildren(option('', 'Не указан'), ...data.departments.map(name => option(name, name)));
      category.replaceChildren(option('', 'Не указана'), ...data.categories.map(item => option(String(item.id), item.name)));
      contractor.replaceChildren(option('', 'Не указан'), ...data.contractors.map(item => option(String(item.id), item.name)));
      crew.replaceChildren(option('', 'Без бригады'), ...crews.rows.map(item => option(String(item.id), item.name + ' · ' + item.owner_name)));
      message.textContent = ''; save.disabled = false; fullName.focus();
    }).catch(error => { if (dialog.isConnected) { message.textContent = error.message; message.classList.add('error-text'); } });
  }
  window.employeeCreator = {open};
})();
