(() => {
  'use strict';
  const screen = window.workforceScreen;
  if (!screen) return;
  const MF = window.MultiFilter;
  const role = document.querySelector('.app-shell').dataset.role;
  const fields = [
    ['project', 'Проект', 'catalog', 'project'], ['department', 'СМУ', 'departments'], ['division', 'Подразделение', 'divisions'],
    ['employer', 'Организация-работодатель', 'organizations'], ['citizenship', 'Гражданство', 'catalog', 'citizenship'],
    ['origin', 'Город отправления', 'catalog', 'travelpoint'], ['profession', 'Должность', 'catalog', 'profession'],
    ['category', 'Категория ГДЛР', 'categories'], ['employment', 'Статус сотрудника', 'catalog', 'employment'],
    ['accommodation', 'Проживание', 'catalog', 'accommodation'],
    ['stage', 'Состояние на дату', 'catalog', 'stage'], ['movement_direction', 'Заезд / выезд', 'direction'],
    ['movement_basis', 'Тип заезда/выезда', 'catalog', 'basis'], ['rotation_schedule', 'График вахтования', 'rotation_schedules'],
  ].filter(([key]) => key !== 'origin' || !['foreman', 'viewer'].includes(role));
  const node = (tag, attrs, text) => {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) el.setAttribute(key, value);
    if (text) el.textContent = text;
    return el;
  };
  const trigger = node('button', {type:'button', id:'wf-export', class:'secondary-button', 'aria-haspopup':'dialog'}, 'Экспорт');
  document.getElementById('wf-heading-actions').append(trigger);
  const dialog = node('dialog', {class:'wf-card wf-export-dialog', 'aria-labelledby':'wf-export-title'});
  const heading = node('h2', {id:'wf-export-title'}, 'Экспорт в Excel');
  const caption = node('p', {}, 'Фильтры применяются ко всем страницам выгрузки. Начальные значения взяты из списка сотрудников.');
  const form = node('form', {class:'wf-export-form'});
  const label = node('label', {}, 'Отчётная дата');
  const day = node('input', {type:'date', required:'', id:'wf-export-date'});
  label.append(day);
  const filters = node('div', {class:'wf-export-filters', 'aria-label':'Фильтры выгрузки'});
  const selects = new Map();
  for (const [key, title] of fields) {
    const field = node('label', {}, title), select = node('select', {id:'wf-export-' + key});
    select.append(new Option('Все значения', '')); field.append(select); filters.append(field);
    MF.enable(select); selects.set(key, select);
  }
  const dates = window.DateFilter.group({fields:screen.dateFields});
  const inherited = node('p', {class:'wf-export-inherited'});
  const status = node('p', {role:'status', 'aria-live':'polite', id:'wf-export-status'});
  const actions = node('div', {class:'wf-export-actions'});
  const close = node('button', {type:'button', class:'secondary-button'}, 'Закрыть');
  const reset = node('button', {type:'button', class:'secondary-button'}, 'Сбросить фильтры');
  const submit = node('button', {type:'submit', class:'primary-button'}, 'Скачать Excel');
  actions.append(reset, close, submit); form.append(label, filters, dates.element, inherited, status, actions); dialog.append(heading, caption, form);
  document.body.append(dialog);
  let query, busy = false, generation = 0;
  function inheritedCaption() {
    const names = {pps:'ППС',full_name:'ФИО',personnel_no:'табельный номер',q:'поиск',conflicts:'замечания',queue:'раздел списка'};
    const active = Object.entries(names).filter(([key]) => query.getAll(key).some(value => value && value !== '0')).map(([,title]) => title);
    inherited.textContent = active.length ? 'Также применяются фильтры списка: ' + active.join(', ') + '. Кнопка «Сбросить фильтры» снимет их только для выгрузки.' : '';
    inherited.hidden = !active.length;
  }
  function setBusy(value) {
    busy = value; submit.disabled = close.disabled = reset.disabled = day.disabled = value;
    for (const select of selects.values()) select.disabled = value;
    dates.controls.forEach(control=>{control.button.disabled=value;});
  }
  trigger.addEventListener('click', async () => {
    if (busy || dialog.open) return;
    const request = ++generation;
    query = screen.listQuery();
    if (['operations', 'lifecycle'].includes(query.get('queue'))) query.delete('queue');
    day.value = query.get('date'); dates.read(query);
    heading.textContent = (query.get('section') === 'recruitment' ? 'Комплектация' : 'Перевахта') + ' · Excel';
    status.textContent = 'Загружаем справочники…';status.classList.remove('error-text');inheritedCaption();dialog.showModal();
    submit.disabled = true; reset.disabled = true;
    try {
      const response = await fetch('/api/workforce/reference', {cache:'no-store'});
      const reference = await window.readApiResponse(response, 'Не удалось загрузить справочники.');
      if (request !== generation || !dialog.open) return;
      for (const [key, , source, kind] of fields) {
        const select = selects.get(key);
        let options = source === 'direction' ? [{value:'arrival',label:'Заезд'},{value:'departure',label:'Выезд'}] :
          (source === 'catalog' ? reference.catalog.filter(row => row.kind === kind) : reference[source] || [])
            .map(row => ({value: source === 'departments' ? row.name : row.code ?? String(row.id),
                          label: (row.label || row.name) + (source === 'divisions' ? ' · '+(row.pps_name || 'ППС не указан') : '') + (row.active === false || row.active === 0 ? ' · архив' : '')}));
        const empty = key === 'stage' ? 'unconfirmed' : '__none__';
        options = [{value:empty,label:key === 'stage' ? 'Без подтверждённого состояния' : 'Без значения справочника'}, ...options];
        for (const value of query.getAll(key)) if (value && !options.some(option => option.value === value)) {
          const original = document.getElementById('wf-' + key);
          options.push({value,label:[...(original?.options || [])].find(option => option.value === value)?.textContent || value});
        }
        select.replaceChildren(new Option('Все значения', ''), ...options.map(option => new Option(option.label, option.value)));
        MF.set(select, query.getAll(key));
      }
      status.textContent = ''; submit.disabled = false; reset.disabled = false;
    } catch (error) { if (request === generation && dialog.open) {status.classList.add('error-text');status.textContent = error.message;} }
  });
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {generation++;});
  reset.addEventListener('click', () => {
    for (const key of [...new Set(query.keys())]) if (!['date','section','sort'].includes(key)) query.delete(key);
    for (const select of selects.values()) MF.set(select, []);
    dates.reset();
    inheritedCaption();status.classList.remove('error-text');status.textContent = 'Фильтры выгрузки сброшены. Фильтры таблицы сохранены.';
  });
  dialog.addEventListener('cancel', event => {if (busy) event.preventDefault();});
  async function queuedRegistry(params) {
    const root = document.querySelector('.app-shell');
    const storageKey = 'registry-export:' + root.dataset.userId + ':' + params.toString();
    let key; try { key = sessionStorage.getItem(storageKey); } catch (_) {}
    if (!key) { key = crypto.randomUUID(); try { sessionStorage.setItem(storageKey, key); } catch (_) {} }
    const body = {type: 'registry', date: params.get('date'), section: params.get('section'),
      filters: [...params.entries()].filter(([name]) => name !== 'date' && name !== 'section'), request_key: key};
    const started = await fetch('/api/workforce/export-jobs', {method: 'POST', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf}, body: JSON.stringify(body)});
    const job = await started.json().catch(() => ({}));
    if (!started.ok) { if (started.status < 500) try { sessionStorage.removeItem(storageKey); } catch (_) {} throw new Error(job.error || 'Не удалось поставить Excel в очередь.'); }
    for (let attempt = 0; attempt < 180; attempt++) {
      const stateResponse = await fetch('/api/workforce/export-jobs/' + key, {cache: 'no-store'});
      const state = await stateResponse.json().catch(() => ({}));
      if (!stateResponse.ok || state.state === 'failed') { try { sessionStorage.removeItem(storageKey); } catch (_) {} throw new Error(state.error || 'Не удалось сформировать Excel.'); }
      if (state.state === 'ready') {
        const file = await fetch('/api/workforce/export-jobs/' + key + '/download', {cache: 'no-store'});
        if (file.ok) try { sessionStorage.removeItem(storageKey); } catch (_) {}
        return file;
      }
      status.textContent = state.state === 'pending' ? 'Excel в очереди…' : 'Формируем Excel…';
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    throw new Error('Excel ещё формируется. Нажмите «Скачать Excel» повторно, чтобы проверить готовность.');
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    setBusy(true);
    status.classList.remove('error-text');status.textContent = 'Формируем Excel…';
    try {
      const params = new URLSearchParams(query);
      params.set('date', day.value); params.delete('limit'); params.delete('offset');
      for (const [key, select] of selects) MF.params(params, key, MF.get(select));
      dates.write(params);
      const response = await queuedRegistry(params);
      if (!response.ok) await window.readApiResponse(response, 'Не удалось сформировать Excel.');
      if (!response.headers.get('Content-Type')?.includes('spreadsheetml.sheet')) {
        throw new Error('Сеанс истёк. Обновите страницу и войдите в приложение.');
      }
      const url = URL.createObjectURL(await response.blob());
      const filename = (params.get('section') === 'recruitment' ? 'Комплектация' : 'Перевахта') + '_' + day.value + '.xlsx';
      const link = node('a', {href:url, download:filename});
      document.body.append(link);link.click();link.remove();setTimeout(() => URL.revokeObjectURL(url), 60000);
      status.textContent = `Файл готов. Сотрудников: ${response.headers.get('X-Export-Row-Count')}.`;
    } catch (error) {
      status.classList.add('error-text');status.textContent = error.message;
    } finally {
      setBusy(false);
    }
  });
})();
