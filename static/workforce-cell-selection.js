(() => {
  'use strict';
  const region = document.getElementById('wf-table-region');
  const table = region?.querySelector('table');
  if (!table) return;
  const body = table.tBodies[0], workspace = document.getElementById('wf-list-workspace');
  const mobile = matchMedia('(max-width: 760px)');
  const tools = document.createElement('div');
  tools.className = 'wf-cell-tools'; tools.hidden = true;
  tools.setAttribute('aria-label', 'Выделение и копирование ячеек');
  const status = document.createElement('span'); status.setAttribute('role', 'status');
  const button = (label, action) => {
    const node = document.createElement('button'); node.type = 'button';
    node.className = 'secondary-button'; node.textContent = label;
    node.addEventListener('click', action); tools.append(node); return node;
  };
  let selected = new Set(), anchor, focus, drag, frame, suppressClick = false, touchMode = false;
  const mode = button('Выделить ячейки', () => {touchMode = !touchMode; clear(); render();});
  mode.classList.add('wf-cell-touch-mode');
  tools.append(status);
  const copy = button('Копировать', copySelection);
  const reset = button('Снять выделение', () => {clear(); region.focus({preventScroll:true});});
  region.before(tools);
  region.title = 'Выделение ячеек: протяните мышью или используйте Shift + клик. Копирование: Ctrl+C. ФИО открывается обычным кликом.';
  const rows = () => [...body.rows];
  const columns = () => [...table.tHead.rows[0].cells].filter(c => c.cellIndex > 0 && !c.classList.contains('wf-column-hidden')).map(c => c.cellIndex);
  const position = cell => ({row:cell.parentElement.sectionRowIndex, column:cell.cellIndex});
  function cellAt(target) {
    const cell = target instanceof Element ? target.closest('td') : null;
    return cell?.parentElement.parentElement === body && cell.cellIndex > 0 &&
      !cell.classList.contains('wf-column-hidden') && !target.closest('input,select,textarea,[contenteditable=true]') ? cell : null;
  }
  function render() {
    body.querySelectorAll('.wf-cell-selected,.wf-cell-focus').forEach(c => c.classList.remove('wf-cell-selected','wf-cell-focus'));
    selected.forEach(c => c.classList.add('wf-cell-selected'));
    if (focus && selected.has(focus)) focus.classList.add('wf-cell-focus');
    tools.hidden = region.hidden || workspace.hidden;
    mode.setAttribute('aria-pressed', String(touchMode));
    mode.textContent = touchMode ? 'Завершить выделение' : 'Выделить ячейки';
    status.textContent = selected.size ? `Ячеек: ${selected.size} · Ctrl+C — копировать` : touchMode ? 'Коснитесь первой и последней ячейки диапазона' : mobile.matches ? '' : 'Выделение: мышью или Shift + клик · Ctrl+C — копировать';
    copy.disabled = reset.disabled = !selected.size;
  }
  function stopDrag() {
    if (frame) cancelAnimationFrame(frame);
    frame = null; drag = null; region.classList.remove('wf-cells-dragging');
  }
  function clear() {stopDrag(); selected.clear(); anchor = focus = null; render();}
  function range(start, end, base = new Set()) {
    const a = position(start), b = position(end), visible = columns(), list = rows();
    selected = new Set(base);
    for (let r = Math.min(a.row,b.row); r <= Math.max(a.row,b.row); r++) {
      for (const c of visible) if (c >= Math.min(a.column,b.column) && c <= Math.max(a.column,b.column) && list[r]?.cells[c]) selected.add(list[r].cells[c]);
    }
    focus = end; render();
  }
  function value(cell) {
    // Warnings in the name cell are presentation, not part of the employee's name.
    const text = cell.querySelector('.wf-person-link')?.textContent ?? cell.innerText;
    const result = text.trim();
    return /[\t\r\n"]/.test(result) ? '"' + result.replaceAll('"','""') + '"' : result;
  }
  function clipboardText() {
    const visible = columns().filter(c => [...selected].some(cell => cell.cellIndex === c));
    return rows().filter(row => [...row.cells].some(cell => selected.has(cell))).map(row =>
      visible.map(c => selected.has(row.cells[c]) ? value(row.cells[c]) : '').join('\t')).join('\r\n');
  }
  async function copySelection() {
    if (!selected.size) return;
    const text = clipboardText();
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else {
        region.focus({preventScroll:true});
        if (!document.execCommand('copy')) throw new Error('copy');
      }
      status.textContent = `Скопировано ячеек: ${selected.size}`;
    } catch (_) {status.textContent = 'Не удалось скопировать. Нажмите Ctrl+C в таблице.'; region.focus({preventScroll:true});}
  }
  function sampleDrag() {
    if (!drag?.moved) return;
    const bounds = region.getBoundingClientRect(), head = table.tHead.getBoundingClientRect();
    // The sticky header must not become the endpoint while scrolling upward.
    const top = Math.max(bounds.top, Math.min(bounds.bottom - 1, head.bottom));
    const x = Math.max(bounds.left + 1, Math.min(bounds.right - 2, drag.x));
    const y = Math.max(top + 1, Math.min(bounds.bottom - 2, drag.y));
    const cell = cellAt(document.elementFromPoint(x,y));
    if (cell && cell !== focus) range(drag.anchor, cell, drag.base);
  }
  function autoScroll() {
    if (!drag?.moved) return;
    const box = region.getBoundingClientRect(), edge = 24;
    const dx = drag.x < box.left + edge ? -12 : drag.x > box.right - edge ? 12 : 0;
    const dy = drag.y < box.top + table.tHead.getBoundingClientRect().height + edge ? -16 : drag.y > box.bottom - edge ? 16 : 0;
    if (dx || dy) {region.scrollBy(dx,dy); sampleDrag();}
    frame = requestAnimationFrame(autoScroll);
  }
  table.addEventListener('pointerdown', event => {
    const cell = cellAt(event.target);
    if (!cell || event.button !== 0 || event.pointerType === 'touch') return;
    const base = event.ctrlKey || event.metaKey ? new Set(selected) : new Set();
    if (!event.shiftKey || !anchor) anchor = cell;
    if ((event.ctrlKey || event.metaKey) && !event.shiftKey && selected.has(cell)) {selected.delete(cell); focus = cell; render();}
    else range(anchor, cell, base);
    drag = {id:event.pointerId, anchor, base, x:event.clientX, y:event.clientY, startX:event.clientX, startY:event.clientY, moved:false};
    suppressClick = event.shiftKey || event.ctrlKey || event.metaKey;
    event.preventDefault(); region.focus({preventScroll:true});
  });
  document.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    drag.x = event.clientX; drag.y = event.clientY;
    if (!drag.moved && Math.hypot(drag.x-drag.startX,drag.y-drag.startY) >= 4) {
      drag.moved = true; suppressClick = true;
      region.classList.add('wf-cells-dragging');
      window.getSelection()?.removeAllRanges();
      frame = requestAnimationFrame(autoScroll);
    }
    if (drag.moved) {event.preventDefault(); sampleDrag();}
  });
  for (const name of ['pointerup','pointercancel']) document.addEventListener(name, () => {
    if (!drag) return;
    stopDrag(); setTimeout(() => {suppressClick = false;},0);
  });
  table.addEventListener('click', event => {
    const cell = cellAt(event.target);
    if (suppressClick) {event.preventDefault(); event.stopImmediatePropagation(); return;}
    if (touchMode && cell) {
      event.preventDefault(); event.stopImmediatePropagation();
      if (!anchor) anchor = cell;
      range(anchor,cell); region.focus({preventScroll:true});
    }
  }, true);
  document.addEventListener('pointerdown', event => {
    if (!region.contains(event.target) && !tools.contains(event.target)) clear();
  });
  document.addEventListener('copy', event => {
    const target = document.activeElement;
    if (!selected.size || (!region.contains(target) && !tools.contains(target)) || target?.matches('input,textarea,select,[contenteditable=true]')) return;
    event.clipboardData?.setData('text/plain', clipboardText());
    event.preventDefault();
    status.textContent = `Скопировано ячеек: ${selected.size}`;
  });
  region.addEventListener('keydown', event => {
    if (event.target !== region) return;
    const list = rows(), visible = columns();
    if (!list.length || !visible.length) return;
    if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); clear(); return;}
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
      event.preventDefault(); event.stopPropagation();
      anchor = list[0].cells[visible[0]]; range(anchor,list.at(-1).cells[visible.at(-1)]); return;
    }
    if (!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    const current = focus ? position(focus) : {row:0,column:visible[0]};
    let r = current.row, c = visible.indexOf(current.column);
    if (event.key === 'ArrowUp') r--;
    if (event.key === 'ArrowDown') r++;
    if (event.key === 'ArrowLeft') c--;
    if (event.key === 'ArrowRight') c++;
    if (event.key === 'Home') {c = 0; if (event.ctrlKey || event.metaKey) r = 0;}
    if (event.key === 'End') {c = visible.length-1; if (event.ctrlKey || event.metaKey) r = list.length-1;}
    const next = list[Math.max(0,Math.min(list.length-1,r))].cells[visible[Math.max(0,Math.min(visible.length-1,c))]];
    if (!next) return;
    if (!event.shiftKey || !anchor) anchor = next;
    range(anchor,next); next.scrollIntoView({block:'nearest',inline:'nearest'});
  });
  new MutationObserver(clear).observe(body,{childList:true});
  let visibleColumns = columns().join(',');
  new MutationObserver(() => {
    const next = columns().join(',');
    if (next !== visibleColumns) {visibleColumns = next; clear();}
  }).observe(table.tHead.rows[0],{subtree:true,attributes:true,attributeFilter:['class']});
  for (const node of [region,workspace]) new MutationObserver(() => {
    if (region.hidden || workspace.hidden) {touchMode = false; clear();} else render();
  }).observe(node,{attributes:true,attributeFilter:['hidden']});
  window.addEventListener('workforce:sectionchange', () => {touchMode = false; clear();});
  window.addEventListener('hashchange',clear);
  window.addEventListener('blur',stopDrag);
  mobile.addEventListener('change',() => {touchMode = false; clear();});
  render();
})();
