(() => {
  'use strict';
  window.createWorkforceBulk = ({api, E, reload, announce}) => {
    let dialog = null, saving = false;
    async function open(rows) {
      if (dialog?.open || saving || !rows.length) return;
      const trigger = document.activeElement;
      const box = E('dialog', {className: 'wf-bulk-dialog', 'aria-labelledby': 'wf-bulk-title'});
      dialog = box;
      const close = E('button', {type: 'button', className: 'secondary-button', onclick: () => box.close()}, 'Отмена');
      const message = E('p', {role: 'status', className: 'wf-bulk-message'}, 'Загрузка выбранных сотрудников…');
      box.append(E('h2', {id: 'wf-bulk-title'}, 'Изменить данные выбранных'), message, close);
      box.addEventListener('cancel', event => {if (saving) event.preventDefault();});
      box.addEventListener('close', () => {box.remove(); if (dialog === box) dialog = null;trigger?.focus({preventScroll: true});});
      document.body.append(box);box.showModal();
      try {
        const data = await api('bulk/prepare', {method: 'POST', body: JSON.stringify({ids: rows.map(row => row.id)})});
        if (!box.open) return;
        const field = E('select', {'aria-label': 'Поле для массового изменения'},
          ...Object.entries(data.fields).map(([value, item]) => E('option', {value}, item.label)));
        const search = E('input', {type: 'search', placeholder: 'Поиск значения в справочнике', 'aria-label': 'Поиск значения в справочнике', maxLength: 200});
        const value = E('select', {required: true, 'aria-label': 'Новое значение из справочника'});
        const note = E('p', {className: 'wf-bulk-hint'});
        const reason = E('textarea', {maxLength: 10000, rows: 2, 'aria-label': 'Причина изменения (необязательно)', placeholder: 'Необязательно для истории сотрудника'});
        const preview = E('tbody');
        const apply = E('button', {type: 'submit', className: 'primary-button', disabled: true}, `Применить выбранным (${data.people.length})`);
        const form = E('form', {className: 'wf-bulk-form'},
          E('div', {className: 'wf-bulk-fields'}, E('label', {}, 'Поле', field), E('label', {}, 'Найти значение', search), E('label', {className:'wf-bulk-wide'}, 'Значение из справочника', value)), note,
          E('div', {className: 'wf-bulk-preview', tabIndex: 0, 'aria-label': 'Предпросмотр изменений'},
            E('table', {}, E('thead', {}, E('tr', {}, E('th', {}, 'Сотрудник'), E('th', {}, 'Сейчас'), E('th', {}, 'Будет'))), preview)),
          E('label', {}, 'Причина изменения (необязательно)', reason), E('div', {className: 'wf-bulk-actions'}, apply, close));
        box.replaceChildren(E('h2', {id: 'wf-bulk-title'}, 'Изменить данные выбранных'),
          E('p', {}, `Выбрано сотрудников: ${data.people.length}. Изменится только выбранное поле.`), form, message);
        message.textContent = '';
        function renderPreview() {
          const title = data.fields[field.value].options.find(option => option.value === value.value)?.label || 'Выберите значение';
          preview.replaceChildren(...data.people.map(row => E('tr', {},
            E('td', {'data-label':'Сотрудник'}, row.name), E('td', {'data-label':'Сейчас'}, row.values[field.value]), E('td', {'data-label':'Будет'}, title))));
          apply.disabled = !value.value;
        }
        function options(reset = false) {
          const selected = reset ? '' : value.value, query = search.value.trim().toLocaleLowerCase('ru');
          const catalog = data.fields[field.value];
          const visible = catalog.options.filter(option => option.value === selected || option.label.toLocaleLowerCase('ru').includes(query));
          value.replaceChildren(E('option', {value: ''}, visible.length ? 'Выберите значение' : 'Совпадений нет'),
            ...visible.map(option => E('option', {value:option.value}, option.label)));
          value.value = selected;
          note.textContent = catalog.note || '';
          renderPreview();
        }
        field.addEventListener('change', () => {search.value = '';options(true);message.textContent = '';});
        search.addEventListener('input', () => options());
        value.addEventListener('change', renderPreview);
        reason.addEventListener('input', renderPreview);
        let attempt = null;
        form.addEventListener('submit', async event => {
          event.preventDefault();if (saving || !form.reportValidity() || !value.value) return;
          const payload = {field:field.value, value:value.value, reference_token:data.fields[field.value].token,
            people:data.people.map(({id,token}) => ({id,token})), reason:reason.value.trim()};
          const signature = JSON.stringify(payload);
          if (attempt?.signature !== signature) attempt = {signature, key:crypto.randomUUID()};
          saving = true;form.inert = true;close.disabled = true;message.textContent = 'Сохранение…';
          try {
            const result = await api('bulk/apply', {method:'POST',body:JSON.stringify({...payload,request_key:attempt.key})});
            box.close();await reload();
            announce(`Обновлено: ${result.changed}. Уже имели это значение: ${result.unchanged}.`);
          } catch (error) {message.textContent = error.message;}
          finally {saving = false;form.inert = false;close.disabled = false;}
        });
        options(true);field.focus();
      } catch (error) {if (box.open) message.textContent = error.message;}
    }
    return {open, busy: () => saving, canLeave: () => {
      if (saving) return false;
      if (dialog?.open) {
        if (!window.confirm('Закрыть массовое редактирование без сохранения?')) return false;
        dialog.close();
      }
      return true;
    }};
  };
})();
