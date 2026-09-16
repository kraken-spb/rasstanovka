(() => {
  'use strict';
  const stages = [['stage.leave', 'Неявка', 'on_leave'], ['stage.inbound', 'Заезд', 'inbound'],
    ['stage.pvp', 'ПВП', 'pvp'], ['stage.onsite', 'Явка', 'onsite'], ['unconfirmed', 'Этап не подтверждён', 'unconfirmed']];
  window.createWorkforceBoard = ({container, api, E, displayDate, openCard, reload}) => {
    const state = {generation: 0, revision: null, query: null, reference: null, lanes: new Map(), selected: new Map(), loading: false,
      saving: false, dragRows: [], dialog: null, selectionBar: null, notice: null};
    const title = code => stages.find(row => row[0] === code)?.[1] || code;
    const writable = row => !!state.reference?.permissions?.transition && !!row.stage_token && row.transition_targets?.length > 0;
    const targets = rows => stages.slice(0, 4).filter(([code]) => rows.length && rows.every(row => writable(row) && row.transition_targets.includes(code)));
    function moscowToday() {
      const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(new Date()).map(part => [part.type, part.value]));
      return `${parts.year}-${parts.month}-${parts.day}`;
    }
    function announce(message) {if (state.notice) state.notice.textContent = message;}
    function syncSelection() {
      for (const checkbox of container.querySelectorAll('[data-wf-select]')) checkbox.checked = state.selected.has(Number(checkbox.dataset.wfSelect));
      for (const card of container.querySelectorAll('[data-wf-worker]')) card.classList.toggle('wf-board-selected', state.selected.has(Number(card.dataset.wfWorker)));
      if (!state.selectionBar) return;
      const rows = [...state.selected.values()];
      state.selectionBar.hidden = !rows.length;
      state.selectionBar.replaceChildren(E('strong', {}, `Выбрано: ${rows.length} / 100`),
        E('button', {type: 'button', className: 'primary-button', disabled: !targets(rows).length, onclick: () => transition(rows)}, 'Изменить этап выбранным'),
        E('button', {type: 'button', className: 'secondary-button', onclick: () => {state.selected.clear();syncSelection();}}, 'Снять выбор'));
      if (rows.length && !targets(rows).length) state.selectionBar.append(E('small', {}, 'Для выбранных сотрудников нет общего доступного перехода.'));
    }
    function toggle(row, selected) {
      if (selected && state.selected.size >= 100 && !state.selected.has(row.id)) {announce('За одно действие можно выбрать до 100 сотрудников.');syncSelection();return;}
      selected ? state.selected.set(row.id, row) : state.selected.delete(row.id);
      syncSelection();
    }
    function clearDrag() {
      state.dragRows = [];
      for (const lane of container.querySelectorAll('.wf-board-lane')) lane.classList.remove('wf-drop-allowed', 'wf-drop-over');
      for (const card of container.querySelectorAll('.wf-person-card')) card.classList.remove('wf-dragging');
    }
    function dragStart(event, row, card) {
      if (state.loading || state.saving || !writable(row) || event.target.closest('button,input,select,textarea')) {event.preventDefault();return;}
      const rows = state.selected.has(row.id) ? [...state.selected.values()] : [row];
      if (!targets(rows).length) {event.preventDefault();announce('Нет общего доступного перехода для выбранных сотрудников.');return;}
      state.dragRows = rows;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(row.id));
      card.classList.add('wf-dragging');
      for (const [code] of targets(rows)) state.lanes.get(code)?.node.classList.add('wf-drop-allowed');
    }
    function cardNode(row, stage) {
      const card = E('article', {className: 'wf-person-card', 'data-wf-worker': String(row.id), draggable: writable(row)});
      const top = E('div', {className: 'wf-board-card-top'}, E('small', {}, row.personnel_no || 'Без табельного номера'));
      if (writable(row)) top.append(E('input', {type: 'checkbox', 'data-wf-select': String(row.id),
        'aria-label': `Выбрать ${row.full_name}`, checked: state.selected.has(row.id), onchange: event => toggle(row, event.target.checked)}));
      card.append(top, E('button', {type: 'button', className: 'wf-person-link', onclick: () => openCard(row.id)}, row.full_name),
        E('small', {}, row.category || 'Категория ГДЛР не указана'), E('small', {}, row.department || 'СМУ не указан'));
      if (row.employer) card.append(E('small', {className: 'wf-board-employer'}, row.employer));
      if (stage === 'stage.pvp') card.append(E('p', {className: 'wf-board-fact'}, row.pvp_address || 'В ПВП'));
      else if (stage === 'stage.onsite') card.append(E('span', {className: 'wf-board-assigned ' + (row.assigned ? 'is-assigned' : '')}, row.assigned ? 'Расставлен' : row.staffing_ready ? 'Ожидает назначения' : 'Не включён в расстановку'));
      else if (row.movement?.planned_date) card.append(E('p', {className: 'wf-board-fact'}, `${row.movement.direction === 'departure' ? 'План выезда' : 'План заезда'}: ${displayDate(row.movement.planned_date)}`));
      if (row.conflicts) card.append(E('small', {className: 'wf-board-warning'}, `Замечаний: ${row.conflicts}`));
      if (row.effective_date) card.append(E('small', {}, 'Событие: ' + displayDate(row.effective_date)));
      if (writable(row)) card.append(E('button', {type: 'button', className: 'wf-board-transition', onclick: () => transition([row])}, 'Изменить этап'));
      card.addEventListener('dragstart', event => dragStart(event, row, card));
      card.addEventListener('dragend', clearDrag);
      return card;
    }
    function renderLane(lane) {
      lane.count.textContent = String(lane.total);
      lane.cards.replaceChildren(...lane.rows.map(row => cardNode(row, lane.code)));
      if (!lane.rows.length) lane.cards.append(E('p', {className: 'wf-board-empty'}, 'Нет сотрудников по выбранным фильтрам'));
      lane.more.hidden = lane.offset >= lane.total;
      lane.more.disabled = lane.loading;
      lane.more.textContent = lane.loading ? 'Загрузка…' : `Показать ещё · ${lane.rows.length} из ${lane.total}`;
      syncSelection();
    }
    async function more(lane) {
      if (lane.loading || state.loading) return;
      const generation = state.generation;
      lane.loading = true; lane.error.textContent = ''; lane.more.disabled = true; lane.more.textContent = 'Загрузка…';
      try {
        const query = new URLSearchParams(state.query);
        query.set('stage', lane.code);query.set('limit', '20');query.set('offset', String(lane.offset));
        const result = await api('people?' + query);
        if (generation !== state.generation) return;
        if (result.revision !== state.revision) {
          await reload();announce('Состав изменился. Доска обновлена; при необходимости повторите выбор сотрудников.');return;
        }
        const ids = new Set(lane.rows.map(row => row.id));
        lane.rows.push(...result.rows.filter(row => !ids.has(row.id)));lane.total = result.totals.total;
        lane.offset = result.rows.length ? lane.offset + result.rows.length : lane.total;
      } catch (err) {if (generation === state.generation) lane.error.textContent = err.message;}
      finally {if (generation === state.generation) {lane.loading = false;renderLane(lane);}}
    }
    function createLane(code, name, data) {
      const count = E('strong', {className: 'wf-board-lane-count'}, String(data.totals.total));
      const header = code === 'unconfirmed' ? E('summary', {className: 'wf-board-lane-heading'}, E('span', {}, name), count) :
        E('header', {className: 'wf-board-lane-heading'}, E('h2', {}, name), count);
      const node = E(code === 'unconfirmed' ? 'details' : 'section', {className: 'wf-board-lane ' + (code === 'unconfirmed' ? 'wf-board-unconfirmed' : code.split('.')[1]), 'aria-label': name}, header);
      const lane = {code, node, count, cards: E('div', {className: 'wf-board-cards'}), rows: data.rows, offset: data.rows.length, total: data.totals.total, loading: false,
        error: E('p', {className: 'error-text wf-lane-error', role: 'alert'}), more: null};
      lane.more = E('button', {type: 'button', className: 'secondary-button wf-board-more', onclick: () => more(lane)}, 'Показать ещё');
      node.append(lane.cards, lane.error, lane.more);
      if (code !== 'unconfirmed') {
        node.addEventListener('dragover', event => {
          if (!state.dragRows.length || !targets(state.dragRows).some(([target]) => target === code)) return;
          event.preventDefault();event.dataTransfer.dropEffect = 'move';node.classList.add('wf-drop-over');
        });
        node.addEventListener('dragleave', event => {if (!node.contains(event.relatedTarget)) node.classList.remove('wf-drop-over');});
        node.addEventListener('drop', event => {
          if (!state.dragRows.length || !targets(state.dragRows).some(([target]) => target === code)) return;
          event.preventDefault();const rows = state.dragRows.slice();clearDrag();transition(rows, code);
        });
      }
      state.lanes.set(code, lane);renderLane(lane);return node;
    }
    async function load(query, reference) {
      const generation = ++state.generation;
      state.query = new URLSearchParams(query);state.query.delete('queue');state.reference = reference;
      state.loading = true;container.inert = true;container.setAttribute('aria-busy', 'true');clearDrag();
      const selectedStage = state.query.get('stage');
      try {
        let responses;
        for (let attempt = 0; attempt < 3; attempt++) {
          responses = await Promise.all(stages.map(async ([code]) => {
            if (selectedStage && selectedStage !== code) return {rows: [], totals: {total: 0}};
            const params = new URLSearchParams(state.query);params.set('stage', code);params.set('limit', '20');params.set('offset', '0');
            return api('people?' + params);
          }));
          if (generation !== state.generation) return null;
          // Do not show a person in two columns when a transition occurred between reads.
          const versions = new Set(responses.filter(row => row.revision != null).map(row => row.revision));
          if (versions.size === 1) {state.revision = [...versions][0];break;}
          if (attempt === 2) throw new Error('Состав сотрудников изменяется. Обновите доску, чтобы показать согласованные данные.');
        }
        if (generation !== state.generation) return null;
        state.selected.clear();state.lanes.clear();
        state.selectionBar = E('div', {className: 'wf-board-selection', hidden: true, 'aria-label': 'Действия с выбранными сотрудниками'});
        state.notice = E('p', {className: 'wf-board-notice', role: 'status', 'aria-live': 'polite'});
        if (reference.permissions?.transition) state.notice.textContent = 'Перетащите карточку в нужный этап или нажмите «Изменить этап». Для группы отметьте сотрудников.';
        const board = E('div', {className: 'wf-board'});
        container.replaceChildren(state.selectionBar, state.notice, board);
        stages.forEach(([code, name], index) => {
          const lane = createLane(code, name, responses[index]);
          if (code === 'unconfirmed') {lane.open = selectedStage === 'unconfirmed';container.append(lane);} else board.append(lane);
        });
        const totals = {total: 0};
        stages.forEach(([, , key], index) => {totals[key] = responses[index].totals.total;totals.total += totals[key];});
        return {totals};
      } catch (err) {
        if (generation === state.generation) {state.selected.clear();container.replaceChildren();}
        throw err;
      } finally {
        if (generation === state.generation) {state.loading = false;container.inert = false;container.setAttribute('aria-busy', 'false');}
      }
    }
    function transition(rows, preferred) {
      if (state.loading || state.saving || state.dialog?.open || !rows.length || rows.length > 100) return;
      const allowed = targets(rows);
      if (!allowed.length) {announce('Для выбранных сотрудников нет доступного перехода. Обновите список.');return;}
      const reportDate = state.query.get('date'), today = moscowToday();
      const maxDate = reportDate < today ? reportDate : today;
      const minDate = rows.map(row => row.effective_date || '').sort().at(-1) || '';
      if (minDate && minDate > maxDate) {announce('Дата текущего этапа позже доступной даты события. Проверьте отчётную дату.');return;}
      const select = E('select', {required: true, name: 'stage_code', id: 'wf-transition-stage'}, ...allowed.map(([code, name]) => E('option', {value: code}, name)));
      if (preferred && allowed.some(([code]) => code === preferred)) select.value = preferred;
      const eventDate = E('input', {type: 'date', required: true, name: 'effective_date', min: minDate, max: maxDate, value: maxDate, id: 'wf-transition-date'});
      const reason = E('textarea', {required: true, maxLength: 10000, name: 'reason', rows: 3, id: 'wf-transition-reason', placeholder: 'Основание и подтверждение события'});
      const message = E('p', {className: 'error-text wf-wide', role: 'alert', hidden: true});
      const submit = E('button', {type: 'submit', className: 'primary-button'}, 'Подтвердить событие');
      const close = E('button', {type: 'button', className: 'secondary-button', 'aria-label': 'Закрыть подтверждение'}, '×');
      const cancel = E('button', {type: 'button', className: 'secondary-button'}, 'Отмена');
      const dialog = E('dialog', {className: 'wf-transition-dialog', 'aria-labelledby': 'wf-transition-title'});
      const form = E('form', {className: 'wf-form wf-transition-form'}, E('label', {}, 'Новый этап', select), E('label', {}, 'Дата события', eventDate),
        E('label', {className: 'wf-wide'}, 'Основание', reason), message, E('div', {className: 'wf-wide wf-transition-actions'}, submit, cancel));
      const list = E('ul', {className: 'wf-transition-people'}, ...rows.map(row => E('li', {}, row.full_name + ' · ' + title(row.stage_code || 'unconfirmed'))));
      dialog.append(E('header', {className: 'wf-card-heading'}, E('h2', {id: 'wf-transition-title'}, 'Изменить текущий этап'), close),
        E('p', {className: 'wf-transition-explanation'}, 'Подтвердите фактическое событие. Будущие заезды и выезды сохраняются отдельно в поездках.'),
        E('details', {className: 'wf-transition-list', open: rows.length === 1}, E('summary', {}, `Сотрудников: ${rows.length} · на ${displayDate(reportDate)}`), list), form);
      let attempt = null;
      const closeDialog = () => {if (!state.saving) dialog.close();};
      close.addEventListener('click', closeDialog);cancel.addEventListener('click', closeDialog);
      dialog.addEventListener('cancel', event => {if (state.saving) event.preventDefault();});
      dialog.addEventListener('close', () => {dialog.remove();if (state.dialog === dialog) state.dialog = null;});
      form.addEventListener('submit', async event => {
        event.preventDefault();if (state.saving || !form.reportValidity()) return;
        if (!reason.value.trim()) {message.textContent = 'Укажите основание события.';message.hidden = false;reason.focus();return;}
        if (eventDate.value > moscowToday() || eventDate.value > reportDate || (minDate && eventDate.value < minDate)) {
          message.textContent = 'Дата события должна быть не раньше текущего этапа и не позже отчётной даты или сегодняшнего дня.';message.hidden = false;return;
        }
        const values = {date: reportDate, effective_date: eventDate.value, stage_code: select.value, reason: reason.value.trim(), people: rows.map(row => ({id: row.id, token: row.stage_token}))};
        const fingerprint = JSON.stringify(values);
        if (!attempt || attempt.fingerprint !== fingerprint) attempt = {fingerprint, key: crypto.randomUUID()};
        state.saving = true;form.inert = true;close.disabled = true;message.hidden = true;submit.textContent = 'Сохранение…';
        try {
          const result = await api('transitions', {method: 'POST', body: JSON.stringify({...values, request_key: attempt.key})});
          dialog.close();state.selected.clear();await reload();
          announce(`Подтверждён этап «${title(result.stage_code)}»: ${result.changed} сотрудников. Дата события: ${displayDate(result.effective_date)}.`);
        } catch (err) {message.textContent = err.message;message.hidden = false;}
        finally {state.saving = false;form.inert = false;close.disabled = false;submit.textContent = 'Подтвердить событие';}
      });
      state.dialog = dialog;document.body.append(dialog);dialog.showModal();select.focus();
    }
    return {load, canLeave: () => {
      if (state.saving) return false;
      if (state.dialog?.open) {
        if (!window.confirm('Закрыть подтверждение перемещения без сохранения?')) return false;
        state.dialog.close();
      }
      return true;
    }, busy: () => state.saving};
  };
})();
