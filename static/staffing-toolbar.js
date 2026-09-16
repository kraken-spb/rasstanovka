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
  for (const id of ['collapse', 'expand', 'undo', 'redo', 'transfer-selected', 'transfer-tomorrow', 'category-selected', 'employer-selected', 'work-selected', 'clear-selected', 'columns-toggle']) {
    const button = document.getElementById('staffing-' + id);
    if (button) content.append(button);
  }
  toolbar.append(menu);
  // Preserve button text and handlers while separating labels from live counts.
  const actions = [...content.querySelectorAll('button')];
  function alignAction(button) {
    const text = button.textContent;
    if (button.firstElementChild?.classList.contains('staffing-menu-label')) return;
    const match = text.match(/^(.*?)\s+(\(\d+\))$/);
    const label = document.createElement('span');
    label.className = 'staffing-menu-label';
    label.textContent = match ? match[1] + ' ' : text;
    button.classList.add('staffing-menu-action');
    if (match) {
      const value = document.createElement('span');
      value.className = 'staffing-menu-value';
      value.textContent = match[2];
      button.replaceChildren(label, value);
    } else button.replaceChildren(label);
  }
  const observer = new MutationObserver(records => {
    const changed = new Set(records.map(record => record.target.nodeType === Node.ELEMENT_NODE
      ? record.target.closest('button') : record.target.parentElement?.closest('button')));
    changed.forEach(button => { if (button && actions.includes(button)) alignAction(button); });
  });
  for (const button of actions) {
    alignAction(button);
    observer.observe(button, {childList: true, characterData: true, subtree: true});
  }
  content.addEventListener('click', event => { if (event.target.closest('button')) menu.open = false; });
  document.addEventListener('click', event => { if (!menu.contains(event.target)) menu.open = false; });
  menu.addEventListener('keydown', event => { if (event.key === 'Escape') { menu.open = false; summary.focus(); } });
})();
