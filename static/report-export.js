(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const panel = $('staffing-export'), form = $('staffing-export-form');
  if (!panel || !form) return;
  const MF = window.MultiFilter, message = $('staffing-export-status');
  let loadedDate = '', pending = null, requestId = 0;
  for (const name of ['category', 'department', 'contractor', 'shift']) {
    MF.enable($('staffing-export-' + name), name === 'shift' ? 'all' : '');
  }
  const status = (text, error = false) => {
    message.textContent = text; message.classList.toggle('error-text', error);
  };
  function options(name, values, all, encode = value => value) {
    const select = $('staffing-export-' + name), selected = MF.values(MF.get(select));
    const choices = values.map(value => [encode(value), value || 'Без категории']);
    for (const value of selected) {
      if (!choices.some(([key]) => key === value)) {
        choices.push([value, [...select.options].find(option => option.value === value)?.textContent || value]);
      }
    }
    select.replaceChildren(new Option(all, ''), ...choices.map(([value, label]) => new Option(label, value)));
    MF.set(select, selected);
  }
  async function ready(refresh = false) {
    const date = $('staffing-export-date').value;
    if (!$('staffing-export-date').reportValidity()) return false;
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
      const includeUnassigned = $('staffing-export-unassigned').checked;
      if (includeUnassigned) params.set('include_unassigned', '1');
      const response = await fetch('/api/staffing/export?' + params, {cache: 'no-store'});
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || 'Не удалось сформировать файл.');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = 'Расстановка на ' + date + '.xlsx';
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      status('Файл с двумя вкладками готов. Строк в списке: ' + response.headers.get('X-Export-Count') +
        (includeUnassigned ? ', нерасставленных сотрудников: ' + response.headers.get('X-Export-Unassigned-Count') : '') + '.');
    } catch (error) { status(error.message, true); }
    finally { button.disabled = false; }
  });
})();
