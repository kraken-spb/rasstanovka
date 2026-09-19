(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const panel = $('staffing-export'), form = $('staffing-export-form');
  if (!panel || !form) return;
  const MF = window.MultiFilter, message = $('staffing-export-status');
  const referenceFields = [
    ['work-type', 'Вид работ'], ['object', 'Группа подобъектов'], ['subobject', 'Подобъект'],
    ['employer', 'Организация-работодатель'], ['profession', 'Должность по штатному расписанию'],
    ['linear-itr', 'ФИО линейного ИТР'], ['brigadier', 'ФИО бригадира']
  ];
  let loadedDate = '', pending = null, requestId = 0;
  const isUrp = () => $('staffing-export-kind')?.value === 'urp';
  for (const name of ['category', 'department', 'contractor', 'shift']) {
    MF.enable($('staffing-export-' + name), name === 'shift' ? 'all' : '');
  }
  let referenceAnchor = $('staffing-export-contractor').closest('label');
  for (const [name, caption] of referenceFields) {
    const select = document.createElement('select');
    select.id = 'staffing-export-' + name;
    const label = document.createElement('label');
    label.dataset.staffingExportReference = name;
    label.append(caption, select);
    referenceAnchor.after(label); referenceAnchor = label;
    MF.enable(select, '');
  }
  const reset = document.createElement('button');
  reset.type = 'button'; reset.id = 'staffing-export-reference-reset'; reset.className = 'secondary-button';
  reset.textContent = 'Сбросить фильтры';
  $('staffing-export-submit').before(reset);
  const status = (text, error = false) => {
    message.textContent = text; message.classList.toggle('error-text', error);
  };
  async function urpReport(date) {
    const root=document.querySelector('.app-shell'), storageKey='urp-export:'+root.dataset.userId+':'+date;
    let key;
    try {key=sessionStorage.getItem(storageKey);} catch (_) {}
    if(!key){key=crypto.randomUUID();try{sessionStorage.setItem(storageKey,key);}catch(_) {}}
    const start=await fetch('/api/workforce/export-jobs',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-CSRF-Token':root.dataset.csrf},body:JSON.stringify({date,request_key:key})});
    const job=await start.json();
    if(!start.ok){if(start.status<500)try{sessionStorage.removeItem(storageKey);}catch(_){}throw new Error(job.error||'Не удалось поставить отчёт в очередь.');}
    for(let attempt=0;attempt<180;attempt++) {
      const response=await fetch('/api/workforce/export-jobs/'+key,{cache:'no-store'}), state=await response.json();
      if(!response.ok||state.state==='failed') {
        try{sessionStorage.removeItem(storageKey);}catch(_){}
        throw new Error(state.error||'Не удалось сформировать отчёт.');
      }
      if(state.state==='ready') {
        const file=await fetch('/api/workforce/export-jobs/'+key+'/download',{cache:'no-store'});
        if(file.ok)try{sessionStorage.removeItem(storageKey);}catch(_){}
        return file;
      }
      status(state.state==='pending'?'Отчёт в очереди…':'Формируем отчёт УРП…');
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
    throw new Error('Отчёт ещё формируется. Нажмите «Экспорт» повторно, чтобы проверить готовность.');
  }
  function options(name, values, all, encode = value => value, preserve = true) {
    const select = $('staffing-export-' + name), selected = MF.values(MF.get(select));
    const choices = values.map(value => typeof value === 'object' ? [value.value, value.label] : [encode(value), value || 'Без категории']);
    if (preserve) {
      for (const value of selected) {
        if (!choices.some(([key]) => key === value)) {
          choices.push([value, [...select.options].find(option => option.value === value)?.textContent || value]);
        }
      }
    }
    select.replaceChildren(new Option(all, ''), ...choices.map(([value, label]) => new Option(label, value)));
    MF.set(select, preserve ? selected : selected.filter(value => choices.some(([key]) => key === value)));
  }
  async function ready(refresh = false) {
    const date = $('staffing-export-date').value;
    if (!$('staffing-export-date').reportValidity()) return false;
    if (isUrp()) return true;
    if (!refresh && loadedDate === date) return true;
    if (!refresh && pending?.date === date) return pending.promise;
    const id = ++requestId;
    status('Загружаем фильтры…');
    const promise = (async () => {
      try {
        const response = await fetch('/api/staffing/export/options?' + new URLSearchParams({date, shift: 'all'}), {cache: 'no-store'});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Не удалось загрузить фильтры экспорта.');
        if (id !== requestId || date !== $('staffing-export-date').value) return false;
        options('department', data.departments, 'Все СМУ');
        options('contractor', data.contractors, 'Все компании-подрядчики');
        options('category', data.categories, 'Все категории', value => value ? 'gdlr:' + value : 'none');
        options('work-type', data.work_types, 'Все виды работ');
        options('object', data.objects, 'Все группы подобъектов');
        options('subobject', data.subobjects, 'Все подобъекты');
        options('employer', data.employers, 'Все организации-работодатели');
        options('profession', data.professions, 'Все должности');
        options('linear-itr', data.linear_itrs, 'Все линейные ИТР');
        options('brigadier', data.brigadiers, 'Все бригадиры');
        loadedDate = date; status(''); return true;
      } catch (error) {
        if (id === requestId) { loadedDate = ''; status(error.message, true); }
        return false;
      } finally { if (id === requestId) pending = null; }
    })();
    pending = {date, promise};
    return promise;
  }
  panel.querySelector('summary').addEventListener('click', () => {
    if (panel.open) return;
    const mode = document.querySelector('[data-summary-mode][aria-pressed="true"]')?.dataset.summaryMode;
    const input = $(['placement', 'category'].includes(mode) ? 'placement-report-date' : mode === 'date' ? 'report-date' : 'dashboard-start');
    if (input?.value) $('staffing-export-date').value = input.value;
    ready(true);
  });
  panel.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !document.querySelector('.multi-filter-dialog')) {
      panel.open = false; panel.querySelector('summary').focus(); event.preventDefault();
    }
  });
  $('staffing-export-date').addEventListener('change', () => ready());
  $('staffing-export-kind')?.addEventListener('change', () => {
    for (const name of ['category', 'department', 'contractor', 'shift', 'unassigned', ...referenceFields.map(([name]) => name)]) {
      const control = $('staffing-export-' + name);
      const label = control?.closest('label');
      if (label) label.hidden = isUrp();
    }
    reset.hidden = isUrp();
    if (isUrp()) {
      for (const name of ['category', 'department', 'contractor', 'shift', ...referenceFields.map(([name]) => name)]) {
        MF.set($('staffing-export-' + name), name === 'shift' ? 'all' : '');
      }
      $('staffing-export-unassigned').checked = false;
    }
    $('staffing-export-help').textContent = isUrp() ?
      'Две вкладки: «Явка и аутстаффинг» и «Неявка, заезд и ПВП». Согласованные 18 категорий ГДЛР, доступные вам СМУ, примечания справа. Учитываются переносы и продления вахт.' :
      'Один Excel-файл: «Список сотрудников» и «Сводная таблица» по этому списку. Все выбранные фильтры применяются к обеим вкладкам.';
    ready();
  });
  reset.addEventListener('click', () => {
    for (const name of ['category', 'department', 'contractor', 'shift', ...referenceFields.map(([name]) => name)]) {
      MF.set($('staffing-export-' + name), name === 'shift' ? 'all' : '');
    }
    $('staffing-export-unassigned').checked = false;
    ready(true);
  });
  window.reportExport = {ready};
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = $('staffing-export-submit');
    if (button.disabled) return;
    button.disabled = true;
    try {
      if (!await ready()) return;
      if (window.staffingScreen && !window.staffingScreen.canLeave()) {
        throw new Error('Завершите текущие изменения в расстановке перед экспортом.');
      }
      status('Формируем файл…');
      const date = $('staffing-export-date').value;
      const params = new URLSearchParams({date});
      MF.params(params, 'shift', MF.get($('staffing-export-shift')) || 'all');
      for (const name of ['department', 'contractor']) MF.params(params, name, MF.get($('staffing-export-' + name)));
      MF.params(params, 'category', MF.get($('staffing-export-category')), value => value === 'none' ? '' : value.slice(5));
      for (const [name] of referenceFields) {
        const key = name === 'work-type' ? 'work_type_id' : name === 'object' ? 'object_id' : name === 'subobject' ? 'subobject_id' : name.replace('-', '_');
        MF.params(params, key, MF.get($('staffing-export-' + name)));
      }
      const includeUnassigned = $('staffing-export-unassigned').checked;
      if (includeUnassigned) params.set('include_unassigned', '1');
      const response = isUrp() ? await urpReport(date) : await fetch('/api/staffing/export?' + params, {cache: 'no-store'});
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || 'Не удалось сформировать файл.');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = (isUrp() ? 'УРП — учёт персонала — ' : 'Расстановка на ') + date + '.xlsx';
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      status(isUrp() ? 'Отчёт УРП готов. Явка и аутстаффинг: ' + response.headers.get('X-Export-First-Count') + ', неявка, заезд и ПВП: ' + response.headers.get('X-Export-Second-Count') + '.' : 'Файл с двумя вкладками готов. Строк в списке: ' + response.headers.get('X-Export-Count') +
        (includeUnassigned ? ', нерасставленных сотрудников: ' + response.headers.get('X-Export-Unassigned-Count') : '') + '.');
    } catch (error) { status(error.message, true); }
    finally { button.disabled = false; }
  });
})();
