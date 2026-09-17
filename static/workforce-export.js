(() => {
  'use strict';
  const screen = window.workforceScreen;
  if (!screen) return;
  const node = (tag, attrs, text) => {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) el.setAttribute(key, value);
    if (text) el.textContent = text;
    return el;
  };
  const trigger = node('button', {type:'button', id:'wf-export', class:'secondary-button', 'aria-haspopup':'dialog'}, 'Экспорт');
  document.querySelector('.wf-heading').append(trigger);
  const dialog = node('dialog', {class:'wf-card wf-export-dialog', 'aria-labelledby':'wf-export-title'});
  const heading = node('h2', {id:'wf-export-title'}, 'Экспорт в Excel');
  const caption = node('p', {}, 'Все сотрудники по текущим фильтрам, со всех страниц списка.');
  const form = node('form', {class:'wf-export-form'});
  const label = node('label', {}, 'Отчётная дата');
  const day = node('input', {type:'date', required:'', id:'wf-export-date'});
  label.append(day);
  const status = node('p', {role:'status', 'aria-live':'polite', id:'wf-export-status'});
  const actions = node('div', {class:'wf-export-actions'});
  const close = node('button', {type:'button', class:'secondary-button'}, 'Закрыть');
  const submit = node('button', {type:'submit', class:'primary-button'}, 'Скачать Excel');
  actions.append(close, submit); form.append(label, status, actions); dialog.append(heading, caption, form);
  document.body.append(dialog);
  let query, busy = false;
  trigger.addEventListener('click', () => {
    query = screen.listQuery();
    day.value = query.get('date');
    heading.textContent = (query.get('section') === 'recruitment' ? 'Комплектование' : 'Перевахта') + ' · Excel';
    status.textContent = '';status.classList.remove('error-text');dialog.showModal();
  });
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('cancel', event => {if (busy) event.preventDefault();});
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    busy = true;submit.disabled = close.disabled = day.disabled = true;
    status.classList.remove('error-text');status.textContent = 'Формируем Excel…';
    try {
      const params = new URLSearchParams(query);
      params.set('date', day.value); params.delete('limit'); params.delete('offset');
      const response = await fetch('/api/workforce/people/export?' + params, {cache:'no-store'});
      if (!response.ok) await window.readApiResponse(response, 'Не удалось сформировать Excel.');
      if (!response.headers.get('Content-Type')?.includes('spreadsheetml.sheet')) {
        throw new Error('Сеанс истёк. Обновите страницу и войдите в приложение.');
      }
      const url = URL.createObjectURL(await response.blob());
      const filename = (params.get('section') === 'recruitment' ? 'Комплектование' : 'Перевахта') + '_' + day.value + '.xlsx';
      const link = node('a', {href:url, download:filename});
      document.body.append(link);link.click();link.remove();setTimeout(() => URL.revokeObjectURL(url), 60000);
      status.textContent = `Файл готов. Сотрудников: ${response.headers.get('X-Export-Row-Count')}.`;
    } catch (error) {
      status.classList.add('error-text');status.textContent = error.message;
    } finally {
      busy = false;submit.disabled = close.disabled = day.disabled = false;
    }
  });
})();
