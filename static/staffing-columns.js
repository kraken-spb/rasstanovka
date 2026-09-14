(() => {
  'use strict';
  const keys = ['number', 'object', 'subobject', 'contractor', 'employer', 'name', 'personnel', 'category', 'itr', 'brigadier', 'shift', 'attendance', 'performed_work', 'assignment_author'];
  const min = 64, max = 800;
  window.staffingColumns = {create({headers, canChange, status}) {
    const root = document.querySelector('.app-shell');
    const key = 'crewplacement.staffing-columns.v1.' + root.dataset.userId;
    const panel = document.getElementById('staffing-columns-panel');
    const list = document.getElementById('staffing-columns-list');
    const desktop = matchMedia('(min-width: 901px)');
    let preferences = {hidden: [], widths: {}, order: [...keys]};
    let table = null, defaults = [], drag = null;
    const controls = [];
    const originalRows = new WeakMap();
    const cellsFor = row => {
      if (!originalRows.has(row)) originalRows.set(row, [...row.cells]);
      return originalRows.get(row);
    };
    const reordered = () => preferences.order.some((id, index) => id !== keys[index]);
    const account = window.staffingPreferences;
    const accountColumns = account.get('columns', null);
    let legacy = false;
    try {
      const saved = accountColumns || JSON.parse(localStorage.getItem(key));
      legacy = !accountColumns && !!saved;
      if (saved && typeof saved === 'object') {
        if (Array.isArray(saved.order)) {
          const known = [...new Set(saved.order.filter(id => keys.includes(id)))];
          preferences.order = [...known, ...keys.filter(id => !known.includes(id))];
        }
        preferences.hidden = keys.filter(id => Array.isArray(saved.hidden) && saved.hidden.includes(id));
        if (preferences.hidden.length === keys.length) preferences.hidden = preferences.hidden.filter(id => id !== 'name');
        keys.forEach(id => {
          const value = saved.widths?.[id];
          if (Number.isFinite(value) && value >= min && value <= max) preferences.widths[id] = Math.round(value);
        });
      }
    } catch (_) { status('Не удалось прочитать настройки столбцов. Используется исходный вид.'); }
    if (legacy) account.set({columns: preferences});
    const visible = index => !preferences.hidden.includes(keys[index]);
    const width = index => preferences.widths[keys[index]] ?? defaults[index] ?? 160;
    function save() {
      account.set({columns: preferences});
    }
    function updateControls() {
      controls.forEach(({checkbox, input, position}, index) => {
        checkbox.checked = visible(index);
        checkbox.disabled = visible(index) && preferences.hidden.length === keys.length - 1;
        input.disabled = !desktop.matches || !visible(index);
        input.value = String(Math.round(width(index)));
        position.value = String(preferences.order.indexOf(keys[index]));
      });
      preferences.order.forEach(id => list.append(controls[keys.indexOf(id)].row));
    }
    function applyWidths() {
      if (!table) return;
      const custom = reordered() || preferences.hidden.length || Object.keys(preferences.widths).length;
      const total = keys.reduce((sum, _, index) => sum + (visible(index) ? width(index) : 0), 0);
      table.style.width = '100%';
      [...table.tHead.rows[0].cells].forEach(cell => {
        const index = Number(cell.dataset.columnStart);
        cell.style.width = custom ? (width(index) / total * 100) + '%' : '';
        cell.querySelector('.staffing-column-resize').setAttribute('aria-valuenow', Math.round(width(index)));
      });
    }
    function applyVisibility() {
      if (!table) return;
      table.querySelectorAll('[data-column-start]').forEach(cell => {
        if (cell.parentElement.classList.contains('staffing-crew-actions-row')) {
          cell.colSpan = keys.length - preferences.hidden.length;
          cell.classList.remove('staffing-column-hidden');
          return;
        }
        const count = keys.slice(Number(cell.dataset.columnStart), Number(cell.dataset.columnEnd) + 1)
          .filter(id => !preferences.hidden.includes(id)).length;
        cell.classList.toggle('staffing-column-hidden', count === 0);
        cell.colSpan = Math.max(1, count);
      });
      applyWidths(); updateControls();
    }
    function setWidth(index, value) {
      preferences.widths[keys[index]] = Math.max(min, Math.min(max, Math.round(value)));
      applyWidths(); updateControls();
    }
    headers.forEach((label, index) => {
      const row = document.createElement('div'); row.className = 'staffing-column-setting';
      const checkLabel = document.createElement('label'); checkLabel.className = 'check-label';
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox';
      checkbox.setAttribute('aria-label', 'Показывать столбец: ' + label);
      checkLabel.append(checkbox, label);
      const input = document.createElement('input'); input.type = 'number'; input.min = min; input.max = max; input.step = '1';
      input.setAttribute('aria-label', 'Ширина столбца в пикселях: ' + label);
      const position = document.createElement('select'); position.className = 'staffing-column-position';
      position.setAttribute('aria-label', 'Позиция столбца: ' + label);
      position.title = 'Порядковый номер столбца';
      keys.forEach((_, order) => {
        const option = document.createElement('option'); option.value = String(order); option.textContent = String(order + 1);
        position.append(option);
      });
      position.addEventListener('change', () => {
        if (!canChange()) { updateControls(); return; }
        const target = Number(position.value), current = preferences.order.indexOf(keys[index]);
        preferences.order.splice(current, 1); preferences.order.splice(target, 0, keys[index]);
        if (table?.isConnected) attach(table); else updateControls();
        save(); position.focus(); status('Порядок столбцов изменён.');
      });
      checkbox.addEventListener('change', () => {
        if (!canChange()) { updateControls(); return; }
        preferences.hidden = checkbox.checked ? preferences.hidden.filter(id => id !== keys[index]) : [...preferences.hidden, keys[index]];
        applyVisibility(); save();
      });
      input.addEventListener('change', () => {
        if (!canChange() || !input.checkValidity() || !Number.isFinite(input.valueAsNumber)) { updateControls(); return; }
        setWidth(index, input.valueAsNumber); save();
      });
      row.append(position, checkLabel, input); list.append(row); controls.push({row, checkbox, input, position});
    });
    document.getElementById('staffing-columns-toggle').addEventListener('click', () => {
      panel.hidden = !panel.hidden;
      document.getElementById('staffing-columns-toggle').setAttribute('aria-expanded', String(!panel.hidden));
      updateControls();
    });
    document.getElementById('staffing-columns-reset').addEventListener('click', () => {
      if (!canChange()) return;
      preferences = {hidden: [], widths: {}, order: [...keys]};
      if (table) attach(table);
      else updateControls();
      save(); status('Исходный порядок, ширина и все столбцы восстановлены.');
    });
    desktop.addEventListener('change', updateControls);
    let resizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => { if (!drag && table?.isConnected) attach(table); }, 120);
    });
    function attach(nextTable) {
      table = nextTable;
      const rows = [...table.rows].filter(row => !row.hasAttribute('data-column-generated'));
      table.querySelectorAll('[data-column-generated]').forEach(node => node.remove());
      rows.forEach(row => {
        const cells = cellsFor(row);
        row.append(...cells);
        let start = 0;
        cells.forEach(cell => {
          if (!cell.hasAttribute('data-column-start')) {
            cell.dataset.columnStart = start; cell.dataset.columnEnd = start + cell.colSpan - 1;
          }
          start = Number(cell.dataset.columnEnd) + 1;
          cell.colSpan = Number(cell.dataset.columnEnd) - Number(cell.dataset.columnStart) + 1;
          cell.classList.remove('staffing-column-hidden');
        });
      });
      table.style.width = '';
      const cells = [...table.tHead.rows[0].cells];
      cells.forEach(cell => { cell.style.width = ''; });
      defaults = cells.map(cell => Math.max(min, Math.round(cell.getBoundingClientRect().width || 160)));
      cells.forEach((cell, index) => {
        cell.querySelector('.staffing-column-resize')?.remove();
        const handle = document.createElement('span'); handle.className = 'staffing-column-resize';
        handle.tabIndex = 0; handle.setAttribute('role', 'separator'); handle.setAttribute('aria-orientation', 'vertical');
        handle.setAttribute('aria-label', 'Ширина столбца: ' + headers[index]);
        handle.setAttribute('aria-valuemin', min); handle.setAttribute('aria-valuemax', max);
        handle.title = 'Перетащите для изменения ширины. Стрелки ← → — шаг 10 пикселей.';
        handle.addEventListener('keydown', event => {
          if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
          event.preventDefault(); if (!canChange()) return;
          setWidth(index, width(index) + (event.key === 'ArrowRight' ? 10 : -10)); save();
        });
        handle.addEventListener('pointerdown', event => {
          if (event.button !== 0 || !desktop.matches || !canChange()) return;
          event.preventDefault();
          drag = {id: event.pointerId, start: event.clientX, width: width(index)};
          handle.setPointerCapture(event.pointerId);
          document.body.classList.add('staffing-column-dragging');
        });
        handle.addEventListener('pointermove', event => {
          if (drag?.id === event.pointerId) setWidth(index, drag.width + event.clientX - drag.start);
        });
        const finish = event => {
          if (drag?.id !== event.pointerId) return;
          drag = null; document.body.classList.remove('staffing-column-dragging'); save();
        };
        handle.addEventListener('pointerup', finish);
        handle.addEventListener('pointercancel', finish);
        handle.addEventListener('lostpointercapture', finish);
        cell.append(handle);
      });
      if (reordered()) {
        const positions = new Map(preferences.order.map((id, position) => [keys.indexOf(id), position]));
        rows.forEach(row => {
          const cells = cellsFor(row), arranged = [];
          cells.forEach(cell => {
            const start = Number(cell.dataset.columnStart), end = Number(cell.dataset.columnEnd);
            if (start !== end && end - start + 1 !== keys.length) {
              // Bulk action controls span several source columns. Give them their own
              // full-width row so any permutation stays aligned and controls stay usable.
              const actions = document.createElement('tr');
              actions.className = 'staffing-crew-assignment staffing-crew-actions-row';
              actions.setAttribute('data-column-generated', '');
              actions.append(cell); row.after(actions);
              for (let index = start; index <= end; index++) {
                const placeholder = document.createElement('td');
                placeholder.setAttribute('data-column-generated', '');
                placeholder.className = 'staffing-column-placeholder';
                placeholder.dataset.columnStart = placeholder.dataset.columnEnd = String(index);
                arranged.push(placeholder);
              }
            } else arranged.push(cell);
          });
          arranged.sort((a, b) => positions.get(Number(a.dataset.columnStart)) - positions.get(Number(b.dataset.columnStart)));
          row.append(...arranged);
        });
      }
      // Card fields follow the same user-selected order on narrow screens.
      table.querySelectorAll('.staffing-worker-row > td').forEach(cell => {
        cell.style.order = reordered() ? String(preferences.order.indexOf(keys[Number(cell.dataset.columnStart)])) : '';
      });
      applyVisibility();
    }
    updateControls();
    return {attach, cell: (row, index) => cellsFor(row)[index]};
  }};
})();
