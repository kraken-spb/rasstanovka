(() => {
  'use strict';
  const table = document.querySelector('#wf-table-region table');
  if (!table) return;
  const account = window.staffingPreferences;
  const panel = document.getElementById('wf-columns-panel');
  const toggle = document.getElementById('wf-columns-toggle');
  const list = document.getElementById('wf-columns-list');
  const desktop = matchMedia('(min-width: 761px)');
  const headers = [...table.tHead.rows[0].cells].slice(1);
  const keys = headers.map(header => header.dataset.column);
  const defaults = headers.map(header => Number(header.dataset.width));
  const controls = [];
  let preferences, scope, drag, hiddenSignature;
  const width = index => preferences.widths[keys[index]] ?? defaults[index];
  const visible = index => !preferences.hidden.includes(keys[index]);
  const scopeKey = () => location.hash.startsWith('#workforce/recruitment') ? 'recruitmentColumns' :
    location.hash.startsWith('#workforce/rotation') ? 'rotationColumns' : 'workforceColumns';
  function read() {
    scope = scopeKey();
    const saved = structuredClone(account.get(scope, {}));
    // Expand the former combined column without changing other saved settings.
    if (Array.isArray(saved.hidden) && saved.hidden.includes('dates')) {
      saved.hidden = [...saved.hidden.filter(key => key !== 'dates'), 'arrival_date', 'forecast_departure_date'];
    }
    if (Number.isInteger(saved.widths?.dates)) {
      for (const key of ['arrival_date', 'forecast_departure_date']) saved.widths[key] ??= Math.max(64, Math.round(saved.widths.dates / 2));
    }
    preferences = {hidden: keys.filter(key => Array.isArray(saved.hidden) && saved.hidden.includes(key)), widths: {}};
    if (preferences.hidden.length === keys.length) preferences.hidden = [];
    keys.forEach(key => {if (Number.isInteger(saved.widths?.[key]) && saved.widths[key] >= 64 && saved.widths[key] <= 800) preferences.widths[key] = saved.widths[key];});
    apply();
  }
  function save() { account.set({[scope]: preferences}, {debounce: true}); }
  function apply() {
    const fitted = !Object.keys(preferences.widths).length;
    const available = Math.max(0, table.parentElement.clientWidth - 44);
    const total = keys.reduce((sum, _, index) => sum + (visible(index) ? width(index) : 0), 0);
    // Keep every visible heading readable; scroll the table instead of squeezing 19 columns.
    const fittedWidth = Math.max(available, total);
    table.style.width = desktop.matches ? (44 + (fitted ? fittedWidth : total)) + 'px' : '';
    [...table.rows].forEach(row => [...row.cells].slice(1).forEach((cell, index) => {
      cell.classList.toggle('wf-column-hidden', !visible(index));
      if (cell.tagName === 'TH') cell.style.width = desktop.matches ?
        (fitted ? (fittedWidth * width(index) / total) + 'px' : width(index) + 'px') : '';
    }));
    const signature = JSON.stringify(preferences.hidden);
    if (signature !== hiddenSignature) {
      hiddenSignature = signature;
      window.dispatchEvent(new Event('workforce:columnschange'));
    }
    controls.forEach(({checkbox, handle}, index) => {
      checkbox.checked = visible(index);
      checkbox.disabled = visible(index) && preferences.hidden.length === keys.length - 1;
      handle.setAttribute('aria-valuenow', Math.round(headers[index].getBoundingClientRect().width || width(index)));
    });
  }
  function freezeWidths() {
    if (Object.keys(preferences.widths).length) return;
    // Start resizing from the displayed layout, without expanding other columns.
    headers.forEach((header, index) => {
      const actual = visible(index) ? header.getBoundingClientRect().width : defaults[index];
      preferences.widths[keys[index]] = Math.max(64, Math.min(800, Math.round(actual)));
    });
  }
  function resize(index, value) {
    preferences.widths[keys[index]] = Math.min(800, Math.max(64, Math.round(value)));
    apply();
  }
  function finishDrag() {
    if (!drag) return;
    const old = drag; drag = null;
    if (old.handle.hasPointerCapture(old.id)) old.handle.releasePointerCapture(old.id);
    document.body.classList.remove('wf-columns-dragging');
    save();
  }
  headers.forEach((header, index) => {
    const title = header.textContent.trim();
    const row = document.createElement('div'); row.className = 'wf-column-setting';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox';
    checkbox.setAttribute('aria-label', 'Показывать столбец: ' + title);
    const label = document.createElement('span'); label.textContent = title;
    const checkLabel = document.createElement('label'); checkLabel.className = 'wf-column-check';
    checkLabel.append(checkbox, label); row.append(checkLabel); list.append(row);
    checkbox.addEventListener('change', () => {
      preferences.hidden = checkbox.checked ? preferences.hidden.filter(key => key !== keys[index]) : [...preferences.hidden, keys[index]];
      apply(); save();
    });
    const handle = document.createElement('span'); handle.className = 'wf-column-resize'; handle.tabIndex = 0;
    handle.setAttribute('role', 'separator'); handle.setAttribute('aria-orientation', 'vertical');
    handle.setAttribute('aria-label', 'Ширина столбца: ' + title);
    handle.setAttribute('aria-valuemin', '64'); handle.setAttribute('aria-valuemax', '800');
    handle.title = 'Перетащите границу или используйте стрелки ← →';
    handle.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); freezeWidths();
      resize(index, event.key === 'Home' ? 64 : event.key === 'End' ? 800 : width(index) + (event.key === 'ArrowRight' ? 10 : -10)); save();
    });
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !event.isPrimary) return;
      event.preventDefault(); handle.focus(); freezeWidths();
      drag = {id: event.pointerId, start: event.clientX, width: width(index), handle};
      handle.setPointerCapture(event.pointerId); document.body.classList.add('wf-columns-dragging');
    });
    handle.addEventListener('pointermove', event => {if (drag?.id === event.pointerId) resize(index, drag.width + event.clientX - drag.start);});
    for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) handle.addEventListener(event, finishDrag);
    header.append(handle); controls.push({checkbox, handle});
  });
  window.ColumnMenu.attach({button:toggle,panel,label:'Столбцы учёта персонала'});
  document.getElementById('wf-columns-reset').addEventListener('click', () => {preferences = {hidden: [], widths: {}}; apply(); save();});
  new MutationObserver(apply).observe(table.tBodies[0], {childList: true});
  new MutationObserver(() => {
    if (document.getElementById('wf-table-region').hidden) {panel.hidden = true; toggle.setAttribute('aria-expanded', 'false');}
  }).observe(document.getElementById('wf-table-region'), {attributes: true, attributeFilter: ['hidden']});
  const notice = document.getElementById('staffing-preferences-status');
  new MutationObserver(() => {
    document.getElementById('wf-columns-status').textContent = notice.textContent;
    document.getElementById('wf-columns-status').classList.toggle('error-text', notice.classList.contains('error-text'));
  }).observe(notice, {childList: true, characterData: true, subtree: true, attributes: true});
  const syncScope = () => {if (scope !== scopeKey()) {finishDrag(); read();}};
  window.addEventListener('hashchange', syncScope);
  // Sidebar navigation uses pushState, which does not emit hashchange.
  window.addEventListener('workforce:sectionchange', syncScope);
  desktop.addEventListener('change', () => {finishDrag(); apply();});
  new ResizeObserver(() => {if (desktop.matches && !Object.keys(preferences.widths).length) apply();}).observe(table.parentElement);
  window.workforceColumns = {isHidden:key=>preferences.hidden.includes(key)};
  read();
})();
