(() => {
  'use strict';
  const toolbar = document.querySelector('#view-staffing > .staffing-toolbar');
  if (!toolbar) return;
  const menu = document.createElement('details');
  menu.className = 'staffing-toolbar-actions';
  const summary = document.createElement('summary');
  summary.textContent = 'Действия';
  const content = document.createElement('div');
  content.className = 'staffing-toolbar-actions-content';
  menu.append(summary, content);
  for (const id of ['collapse', 'expand', 'undo', 'redo', 'transfer-selected', 'transfer-tomorrow', 'category-selected', 'work-selected', 'clear-selected', 'columns-toggle']) {
    const button = document.getElementById('staffing-' + id);
    if (button) content.append(button);
  }
  toolbar.append(menu);
  content.addEventListener('click', event => { if (event.target.closest('button')) menu.open = false; });
  document.addEventListener('click', event => { if (!menu.contains(event.target)) menu.open = false; });
  menu.addEventListener('keydown', event => { if (event.key === 'Escape') { menu.open = false; summary.focus(); } });
})();
