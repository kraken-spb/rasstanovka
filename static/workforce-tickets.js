(() => {
  'use strict';
  // The same optional ticket popup is used by the board, table and employee card.
  window.createWorkforceTickets = ({E, rows, reference, prefix = 'wf-ticket', allowDirectArrival = false, onChange = () => {}}) => {
    let selectedStage = '', dialog = null;
    const saved = new Map();
    const direction = () => selectedStage === 'stage.outbound' ? 'departure' : 'arrival';
    const eligible = row => ['stage.inbound', 'stage.pvp', 'stage.outbound'].includes(selectedStage)
      || (allowDirectArrival && selectedStage === 'stage.onsite' && row.stage_code === 'stage.leave');
    const selected = () => rows.filter(eligible);
    const values = () => selected().filter(row => saved.has(row.id)).map(row => structuredClone(saved.get(row.id)));
    const hint = E('span', {className: 'wf-ticket-entry-hint'});
    const launch = E('button', {type: 'button', className: 'secondary-button'}, 'Добавить билет');
    const element = E('div', {className: 'wf-wide wf-ticket-entry', hidden: true}, launch, hint);
    function update(stage = selectedStage) {
      selectedStage = stage;
      element.hidden = !selected().length;
      const count = values().length;
      launch.textContent = count ? `Билеты · ${count}` : 'Добавить билет';
      hint.textContent = count ? 'Сохранятся вместе с состоянием' : 'Необязательно · вручную или из файла';
    }
    function show() {
      if (dialog?.open) return;
      const people = selected();
      if (!people.length) return;
      const controls = new Map();
      const error = E('p', {role: 'alert', className: 'wf-ticket-error'});
      const form = E('form', {className: 'wf-form wf-ticket-dialog-form'});
      dialog = E('dialog', {className: 'wf-ticket-dialog', 'aria-label': 'Билеты сотрудников'}, form);
      const currentDialog = dialog;
      const close = () => currentDialog.close();
      const closeButton = E('button', {type: 'button', className: 'secondary-button'}, 'Закрыть');
      closeButton.onclick = close;
      form.append(E('div', {className: 'wf-wide wf-ticket-dialog-heading'}, E('h2', {}, `Билеты · ${people.length}`), closeButton),
        E('p', {className: 'wf-wide'}, 'Заполните только при наличии билета. Даты поездки — плановые; состояние сотрудника изменится с даты, выбранной в календаре.'));
      const travelPoint = label => E('select', {'aria-label': label}, E('option', {value: ''}, 'Не указан'),
        ...(reference.catalog || []).filter(item => item.kind === 'travelpoint' && item.active).map(item => E('option', {value: item.code}, item.label)));
      const upload = E('button', {type: 'button', className: 'secondary-button'}, 'Загрузить и распознать');
      upload.onclick = () => {
        if (!window.WorkforceTicketImport) {error.textContent = 'Распознавание недоступно. Можно заполнить билет вручную.';return;}
        window.WorkforceTicketImport.open({people, direction: direction(),
          presets: [...controls.values()].filter(c => c.enabled.checked && c.recognition).map(c => c.recognition),
          onSelect: items => {
            if (!currentDialog.isConnected || !currentDialog.open) throw Error('Окно смены состояния закрыто. Откройте его снова.');
            for (const item of items) {
              const c = controls.get(Number(item.worker_id));
              if (!c) throw Error('Сотрудник не входит в выбранный состав.');
              c.recognition = structuredClone(item);c.enabled.checked = true;
              const first = item.segments[0], last = item.segments[item.segments.length - 1];
              c.planned.value = item.direction === 'arrival' ? last.arrival_date : first.departure_date;
              c.origin.value = '';c.destination.value = '';
              c.details.value = [(item.transport === 'air' ? 'Авиа' : 'ЖД') + ' · ' + item.ticket_number, item.passenger,
                ...item.segments.map(s => `${s.origin} → ${s.destination} · ${s.departure_date} ${s.departure_time || ''} — ${s.arrival_date} ${s.arrival_time || ''}`
                  + (s.flight ? ` · рейс/поезд ${s.flight}` : '') + (s.coach ? ` · вагон ${s.coach}` : '') + (s.seat ? ` · место ${s.seat}` : ''))].join('\n');
              refresh(c);
            }
            error.textContent = 'Распознанные данные подготовлены. Нажмите «Использовать при смене состояния».';
          }});
      };
      form.append(E('div', {className: 'wf-wide'}, upload));
      if (people.length > 1) {
        const planned = E('input', {type: 'date', 'aria-label': 'Общая дата по билету'});
        const origin = travelPoint('Общее место отправления'), destination = travelPoint('Общее место прибытия');
        const apply = E('button', {type: 'button', className: 'secondary-button'}, 'Заполнить пустые поля');
        const feedback = E('span', {className: 'wf-ticket-common-feedback', role: 'status'});
        apply.onclick = () => {
          let changed = 0;
          for (const c of controls.values()) {
            if (!c.enabled.checked || c.recognition) continue;
            let updated = false;
            for (const [key, source] of Object.entries({planned, origin, destination})) if (source.value && !c[key].value) {c[key].value = source.value;updated = true;}
            if (updated) changed++;
          }
          feedback.textContent = changed ? `Заполнено строк: ${changed}. Уже введённые значения сохранены.` : 'Нет пустых полей для заполнения.';
        };
        form.append(E('fieldset', {className: 'wf-wide wf-ticket-common'}, E('legend', {}, 'Общие дата и маршрут'),
          E('label', {}, 'Дата по билету', planned), E('label', {}, 'Откуда', origin), E('label', {}, 'Куда', destination), apply, feedback));
      }
      const body = E('tbody');
      const cell = (label, input) => E('td', {}, E('label', {}, E('span', {className: 'wf-ticket-cell-label'}, label), input));
      function refresh(c) {
        const enabled = c.enabled.checked, recognized = Boolean(c.recognition);
        for (const key of ['planned', 'origin', 'destination', 'details']) c[key].disabled = !enabled || (recognized && ['origin', 'destination'].includes(key));
        c.planned.readOnly = recognized;c.details.readOnly = recognized;
        c.manual.hidden = !recognized;
        c.origin.options[0].textContent = recognized ? c.recognition.segments[0].origin : 'Не указан';
        c.destination.options[0].textContent = recognized ? c.recognition.segments.at(-1).destination : 'Не указан';
        c.row.classList.toggle('wf-ticket-disabled', !enabled);
      }
      for (const person of people) {
        const value = saved.get(person.id);
        const enabled = E('input', {type: 'checkbox', checked: Boolean(value), 'aria-label': 'Билет есть: ' + person.full_name});
        const planned = E('input', {type: 'date', required: true, value: value?.planned_date || '', id: `${prefix}-date-${person.id}`, 'aria-label': 'Дата по билету: ' + person.full_name});
        const origin = travelPoint('Откуда: ' + person.full_name), destination = travelPoint('Куда: ' + person.full_name);
        origin.value = value?.origin_code || '';destination.value = value?.destination_code || '';
        const details = E('textarea', {required: true, maxLength: 5000, rows: 3, value: value?.travel_details || '',
          'aria-label': 'Билет / транспорт: ' + person.full_name, placeholder: 'Номер билета, рейс или поезд, время'});
        details.oninput = () => details.setCustomValidity('');
        const manual = E('button', {type: 'button', className: 'secondary-button wf-ticket-manual'}, 'Перейти к ручному вводу');
        const row = E('tr', {'data-ticket-worker': String(person.id)}, E('th', {scope: 'row'}, E('strong', {}, person.full_name),
          E('small', {}, person.personnel_no ? 'Таб. № ' + person.personnel_no : 'Без табельного номера'),
          E('label', {className: 'wf-ticket-toggle'}, enabled, 'Билет есть')), cell('Дата по билету', planned),
          cell('Откуда', origin), cell('Куда', destination), cell('Билет / транспорт', details));
        row.lastChild.append(manual);body.append(row);
        const c = {enabled, planned, origin, destination, details, row, manual, recognition: value?.recognition ? structuredClone(value.recognition) : null};
        enabled.onchange = () => refresh(c);
        manual.onclick = () => {c.recognition = null;refresh(c);};
        controls.set(person.id, c);refresh(c);
      }
      const cancel = E('button', {type: 'button', className: 'secondary-button'}, 'Отмена');cancel.onclick = close;
      const submit = E('button', {type: 'submit', className: 'primary-button'}, 'Использовать при смене состояния');
      form.append(E('table', {className: 'wf-wide wf-ticket-table', 'aria-label': 'Билеты выбранных сотрудников'},
        E('thead', {}, E('tr', {}, ...['Сотрудник', 'Дата по билету', 'Откуда', 'Куда', 'Билет / транспорт'].map(text => E('th', {scope: 'col'}, text)))), body),
        error, E('div', {className: 'wf-wide wf-ticket-dialog-actions'}, cancel, submit));
      form.onsubmit = event => {
        event.preventDefault();
        const next = new Map();
        for (const [id, c] of controls) {
          if (!c.enabled.checked) continue;
          c.details.setCustomValidity(c.details.value.trim() ? '' : 'Укажите реквизиты билета.');
          if (!c.planned.reportValidity() || !c.details.reportValidity()) return;
          if (c.recognition && c.recognition.direction !== direction()) {error.textContent = 'Направление билета не соответствует состоянию. Проверьте билет в окне распознавания.';return;}
          next.set(id, {worker_id: id, planned_date: c.planned.value, origin_code: c.origin.value, destination_code: c.destination.value,
            travel_details: c.details.value.trim(), ...(c.recognition ? {recognition: structuredClone(c.recognition)} : {})});
        }
        for (const id of controls.keys()) {saved.delete(id);if (next.has(id)) saved.set(id, next.get(id));}
        update();onChange();close();
      };
      const observer = new MutationObserver(() => {if (!element.isConnected) close();});
      currentDialog.addEventListener('close', () => {observer.disconnect();currentDialog.remove();if (dialog === currentDialog) dialog = null;if (element.isConnected) launch.focus();}, {once: true});
      document.body.append(currentDialog);currentDialog.showModal();observer.observe(document.body, {childList: true, subtree: true});
    }
    launch.onclick = show;
    return {element, update, values, validate: () => {
      if (values().some(item => item.recognition && item.recognition.direction !== direction())) {show();return false;}
      return true;
    }};
  };
})();
