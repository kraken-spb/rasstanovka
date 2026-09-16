/* Position cards reuse the export window's date and filters. */
(() => {
  'use strict';
  const form = document.getElementById('staffing-export-form');
  if (!form || document.getElementById('position-cards-export')) return;
  const button = document.createElement('button');
  button.type = 'button';
  button.id = 'position-cards-export';
  button.className = 'secondary-button';
  button.textContent = 'Карточки позиций · PDF';
  const help = document.createElement('p');
  help.className = 'muted';
  help.style.cssText = 'flex:1 1 100%;min-width:0;margin:0;';
  help.textContent = 'Компактный PDF по СМУ: позиции идут подряд, рядом — смены, ответственные, работы и состав сотрудников.';
  form.append(button, help);

  const selected = id => Array.from(document.getElementById(id).selectedOptions, option => option.value);
  button.addEventListener('click', async () => {
    const excel = document.getElementById('staffing-export-submit');
    if (button.disabled || excel.disabled) return;
    const status = document.getElementById('staffing-export-status');
    if (window.staffingScreen && !window.staffingScreen.canLeave()) {
      status.textContent = 'Завершите текущие изменения в расстановке перед экспортом.';
      status.classList.add('error-text');
      return;
    }
    if (window.reportExport && !await window.reportExport.ready()) return;
    if (button.disabled || excel.disabled) return;
    const dateInput = document.getElementById('staffing-export-date');
    if (!dateInput.reportValidity() || !dateInput.value) return;
    const params = new URLSearchParams({date: dateInput.value});
    for (const field of ['department', 'contractor', 'shift']) {
      for (const value of selected('staffing-export-' + field)) {
        if (value) params.append(field, value);
      }
    }
    for (const value of selected('staffing-export-category')) {
      if (value) params.append('category', value === 'none' ? '' : value.slice(5));
    }
    button.disabled = excel.disabled = true;
    status.classList.remove('error-text');
    status.textContent = 'Формируем карточки позиций…';
    try {
      const response = await fetch('/api/staffing/position-cards/pdf?' + params, {cache: 'no-store'});
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || 'Не удалось сформировать PDF.');
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'Карточки позиций ' + dateInput.value + '.pdf';
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      status.textContent = 'PDF готов. Карточек: ' + response.headers.get('X-Export-Card-Count') +
        ', назначений: ' + response.headers.get('X-Export-Count') + '.';
    } catch (error) {
      status.textContent = error.message;
      status.classList.add('error-text');
    } finally {
      button.disabled = excel.disabled = false;
    }
  });
})();
