(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('backup-panel')) return;
  const root = document.querySelector('.app-shell');
  const state = {busy: false, page: 1};
  const dateTime = new Intl.DateTimeFormat('ru-RU', {timeZone: 'Europe/Moscow',
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'});
  const el = (tag, text, className = '') => {
    const node = document.createElement(tag); node.textContent = text; node.className = className; return node;
  };
  function status(message, error = false) {
    $('backups-status').textContent = message;
    $('backups-status').classList.toggle('error-text', error);
  }
  function busy(value) {
    state.busy = value;
    $('backups-create').disabled = value;
    $('backups-refresh').disabled = value;
    $('backups-date').disabled = value;
    pager.setBusy(value);
  }
  async function api(url, options = {}) {
    const response = await fetch(url, {...options, cache: 'no-store', headers: {
      'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf,
    }});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось выполнить операцию с резервными копиями.');
    return data;
  }
  const pager = window.TablePagination.mount($('backups-list'), 'Резервные копии', async nextPage => {
    if (state.busy) return false;
    state.page = nextPage + 1;
    await load();
  }, {top: false, sizes: false, container: document.querySelector('.backup-pagination')});
  function render(data) {
    const list = $('backups-list');
    list.replaceChildren();
    pager.update(data.total, data.page - 1, data.page_size);
    if (!data.rows.length) { list.append(el('p', 'Резервных копий пока нет.', 'empty-state')); return; }
    const table = document.createElement('table'); table.className = 'backup-table';
    const head = document.createElement('thead'); const heading = document.createElement('tr');
    const labels = ['Создана (МСК)', 'Расставлено, чел.', 'Размер', 'Копия'];
    if (data.can_download) labels.push('');
    labels.forEach(label => { const th = el('th', label); th.scope = 'col'; heading.append(th); });
    head.append(heading); table.append(head);
    const body = document.createElement('tbody');
    data.rows.forEach(item => {
      const row = document.createElement('tr');
      const cell = (label, text) => { const td = el('td', text); td.dataset.label = label; row.append(td); return td; };
      const when = cell(labels[0], dateTime.format(new Date(item.created_at)));
      if (item.time_source === 'file') when.append(el('small', 'Время файла'));
      const count = cell(labels[1], item.status === 'ready' ? String(item.employee_count) : 'Не определено');
      if (item.status === 'ready') count.append(el('small', 'Назначений по сменам: ' + item.assignment_count));
      else count.append(el('small', 'Не удалось прочитать копию', 'error-text'));
      cell(labels[2], new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}).format(item.size_bytes / 1024 / 1024) + ' МБ');
      const file = cell(labels[3], item.reason === 'manual' ? 'Вручную' : item.reason === 'automatic' ? 'Автоматическая' : 'Существующая');
      file.append(el('small', item.name, 'backup-filename'));
      if (data.can_download) {
        const action = cell('Действие', '');
        const link = el('a', 'Скачать', 'secondary-button backup-download');
        link.href = '/api/backups/' + encodeURIComponent(item.name) + '/download';
        link.setAttribute('download', item.name); action.append(link);
      }
      body.append(row);
    });
    table.append(body); list.append(table);
  }
  async function fetchList() {
    const data = await api('/api/backups?' + new URLSearchParams({date: $('backups-date').value, page: state.page}));
    render(data);
  }
  async function load() {
    if (state.busy) return;
    busy(true); status('Загрузка резервных копий…');
    try { await fetchList(); status(''); }
    catch (error) { $('backups-list').replaceChildren(); status(error.message, true); }
    finally { busy(false); }
  }
  $('backups-create').addEventListener('click', async () => {
    if (state.busy || !$('backups-date').reportValidity()) return;
    busy(true); status('Создание резервной копии…');
    try {
      const saved = await api('/api/backups', {method: 'POST', body: JSON.stringify({date: $('backups-date').value})});
      state.page = 1;
      await fetchList();
      status('Бэкап создан. Расставлено на выбранную дату: ' + saved.employee_count + ' чел.');
    } catch (error) { status(error.message, true); }
    finally { busy(false); }
  });
  $('backups-refresh').addEventListener('click', () => load());
  $('backups-date').addEventListener('change', () => { state.page = 1; load(); });
  window.backupsScreen = {load, canLeave: () => {
    if (state.busy) { status('Дождитесь завершения операции с резервными копиями.'); return false; }
    return true;
  }};
})();
