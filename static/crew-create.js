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
    children.flat().forEach(child => node.append(child));
    return node;
  };
  async function api(url, options = {}) {
    const response = await fetch(url, {...options, cache: 'no-store', headers: {
      'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось создать бригаду.');
    return data;
  }
  function open({rows, snapshot, onSaved, onClosed}) {
    let saving = false, loaded = false, people = [];
    const name = el('input', {id: 'create-crew-name', required: true, maxLength: 120, autocomplete: 'off'});
    function personField(id, title) {
      let selected = null;
      const input = el('input', {id, maxLength: 200, autocomplete: 'off', placeholder: 'Фамилия или Regex: Иванов Петров',
        'aria-describedby': id + '-help ' + id + '-error'});
      const manual = el('input', {type: 'checkbox', id: id + '-manual'});
      const error = el('p', {id: id + '-error', className: 'error-text', role: 'alert', hidden: true});
      const matches = el('div', {id: id + '-matches', className: 'crew-create-matches', hidden: true,
        'aria-label': 'Найденные сотрудники: ' + title});
      function show() {
        error.hidden = true; matches.replaceChildren(); matches.hidden = true;
        if (manual.checked || !input.value.trim() || selected) return;
        let expression;
        try { expression = new RegExp(SearchRegex.source(input.value), 'iu'); }
        catch (_) { error.textContent = 'Некорректный Regex. Исправьте выражение.'; error.hidden = false; return; }
        if (!loaded) { error.textContent = 'Список сотрудников ещё загружается.'; error.hidden = false; return; }
        const found = people.filter(person => expression.test(person.full_name));
        matches.hidden = false;
        matches.append(el('p', {className: 'table-note'}, found.length ? 'Найдено: ' + found.length + (found.length > 30 ? '. Показаны первые 30 — уточните запрос.' : '') : 'Совпадений нет. Измените запрос или включите «Вручную».'));
        found.slice(0, 30).forEach(person => matches.append(el('button', {type: 'button', className: 'crew-create-person', onclick: () => {
          if (saving) return;
          selected = person; input.value = person.full_name; show(); input.focus();
        }}, el('strong', {}, person.full_name), el('small', {}, 'Таб. № ' + (person.personnel_no || '—') + ' · ' + (person.profession || person.position || person.department || 'Должность не указана')))));
      }
      input.addEventListener('input', () => { selected = null; show(); });
      input.addEventListener('focus', show);
      input.addEventListener('keydown', event => {
        if (event.key === 'ArrowDown' && !matches.hidden) { event.preventDefault(); matches.querySelector('button')?.focus(); }
        if (event.key === 'Enter' && !selected && !manual.checked && input.value.trim()) {
          event.preventDefault(); show(); matches.querySelector('button')?.focus();
        }
        if (event.key === 'Escape' && !matches.hidden) { event.preventDefault(); event.stopPropagation(); matches.hidden = true; }
      });
      manual.addEventListener('change', () => { if (saving) return; selected = null; show(); });
      const container = el('div', {className: 'crew-create-person-field'}, el('label', {}, title, input),
        el('label', {className: 'check-label crew-create-manual'}, manual, 'Вручную'),
        el('p', {id: id + '-help', className: 'table-note'}, 'Regex без учёта регистра. Варианты через пробел или запятую: Иванов Петров. Для пробела внутри шаблона используйте [ ]. Выберите ФИО из результатов.'), error, matches);
      return {container, input, show, valid() {
        if (!input.value.trim() || manual.checked || selected) return true;
        show(); if (error.hidden) { error.textContent = 'Выберите ФИО из результатов поиска или включите «Вручную».'; error.hidden = false; }
        input.focus(); return false;
      }, value: () => input.value.trim(), personId: () => selected?.id ?? null,
      disable(value) { input.disabled = value; manual.disabled = value; matches.querySelectorAll('button').forEach(button => { button.disabled = value; }); }};
    }
    const itr = personField('create-crew-itr', 'Линейный ИТР');
    const brigadier = personField('create-crew-brigadier', 'Бригадир');
    const message = el('p', {role: 'status', className: 'save-status'});
    const save = el('button', {type: 'submit', className: 'primary-button', disabled: true},
      rows.length ? 'Создать и включить сотрудников (' + rows.length + ')' : 'Создать бригаду');
    const close = () => { if (saving) return; dialog.close(); dialog.remove(); onClosed(); };
    const cancel = el('button', {type: 'button', className: 'secondary-button', onclick: close}, 'Отмена');
    const label = (text, input) => el('label', {}, text, input);
    const list = el('ul', {className: 'crew-create-members'}, ...rows.map(row => el('li', {},
      el('strong', {}, row.full_name), el('span', {}, 'Таб. № ' + (row.personnel_no || '—') + ' · ' + (row.crew_name || 'Без бригады')))));
    const preview = el('details', {open: !!rows.length}, el('summary', {}, 'Состав новой бригады: ' + rows.length + ' чел.'),
      rows.length ? list : el('p', {className: 'table-note'}, 'Создаётся пустая бригада. Сотрудников можно добавить позже.'));
    const form = el('form', {className: 'stack-form'},
      label('Название бригады', name), itr.container, brigadier.container, preview,
      el('p', {className: 'table-note'}, 'Отмеченные сотрудники переходят из текущих бригад в новую. Назначения на даты и индивидуальные корректировки ИТР и бригадира сохраняются.'),
      message, el('div', {className: 'crew-create-actions'}, cancel, save));
    const dialog = el('dialog', {id: 'create-crew-dialog', className: 'app-dialog crew-create-dialog', 'aria-labelledby': 'create-crew-title'},
      el('h2', {id: 'create-crew-title'}, rows.length ? 'Создать бригаду из выбранных' : 'Создать бригаду'), form);
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (saving || !loaded || !form.reportValidity()) return;
      if (!itr.valid() || !brigadier.valid()) return;
      if (!name.value.trim()) { name.setCustomValidity('Введите название бригады.'); name.reportValidity(); return; }
      saving = true;
      for (const control of [name, save, cancel]) control.disabled = true;
      itr.disable(true); brigadier.disable(true);
      message.classList.remove('error-text'); message.textContent = 'Создание бригады…';
      let result;
      try {
        result = await api('/api/crews', {method: 'POST', body: JSON.stringify({name: name.value.trim(),
          linear_itr: itr.value(), brigadier: brigadier.value(),
          linear_itr_person_id: itr.personId(), brigadier_person_id: brigadier.personId(),
          worker_ids: rows.map(row => row.id), ...snapshot})});
      } catch (error) {
        message.textContent = error.message;
        message.classList.add('error-text');
      } finally {
        saving = false;
        for (const control of [name, save, cancel]) control.disabled = false;
        itr.disable(false); brigadier.disable(false);
      }
      if (result) { close(); await onSaved(result); }
    });
    name.addEventListener('input', () => name.setCustomValidity(''));
    document.body.append(dialog); dialog.showModal();
    api('/api/staffing/people').then(data => {
      if (!dialog.isConnected) return;
      people = data.people;
      loaded = true; save.disabled = false;
      if (!people.length) message.textContent = 'Справочник ФИО пуст. Можно оставить ответственных пустыми или указать их вручную.';
      itr.show(); brigadier.show();
    }).catch(error => { if (dialog.isConnected) { message.textContent = error.message; message.classList.add('error-text'); } });
    name.focus();
  }
  window.crewCreator = {open};
})();
