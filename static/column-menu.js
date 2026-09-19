(() => {
  'use strict';
  let active = null, sequence = 0;
  window.ColumnMenu = {attach({button, panel, label = 'Столбцы', onOpen, owner}) {
    owner ||= button.closest('.view');
    const toolbar = owner?.querySelector({
      'view-staffing': '.staffing-toolbar', 'view-workforce': '.wf-filter-actions',
      'view-employees': '.employees-toolbar', 'view-outstaff': '.employees-toolbar'
    }[owner.id] || '.column-menu-toolbar');
    let controls = button;
    if (toolbar) {
      const sorting = owner.querySelector('.table-sort-toggle');
      if (sorting) {
        controls = document.createElement('div');
        controls.className = 'table-view-controls';
        controls.append(sorting, button);
      }
      toolbar.append(controls);
      button.classList.remove('table-pagination-columns');
      button.classList.add('column-menu-toggle', 'ui-action', 'ui-icon-only');
      button.dataset.uiIcon = 'columns';
      button.title = 'Настроить столбцы';button.setAttribute('aria-label', 'Столбцы');
    }
    const tableRegion = owner?.id === 'view-workforce' ? owner.querySelector('#wf-table-region') : null;
    if (tableRegion) controls.hidden = tableRegion.hidden;
    const home = document.createComment('column-menu');
    panel.before(home);
    const popup = document.createElement('div');
    popup.id = 'column-menu-' + ++sequence;
    popup.className = 'column-menu-popup';
    popup.setAttribute('popover', 'auto');
    popup.setAttribute('role', 'dialog');
    popup.setAttribute('aria-label', label);
    const heading = document.createElement('strong');heading.className = 'column-menu-title';heading.textContent = 'Столбцы';
    panel.classList.add('column-menu-content');panel.hidden = true;
    popup.append(heading, panel);document.body.append(popup);
    button.setAttribute('aria-haspopup', 'dialog');button.setAttribute('aria-controls', popup.id);button.setAttribute('aria-expanded', 'false');
    let opened = false;
    function position() {
      if (!opened) return;
      const anchor = button.getBoundingClientRect(), edge = 10, gap = 6;
      const width = Math.min(340, innerWidth - edge * 2);
      const below = innerHeight - anchor.bottom - gap - edge, above = anchor.top - gap - edge;
      const downward = below >= 280 || below >= above;
      popup.style.width = width + 'px';
      popup.style.maxHeight = Math.max(100, Math.min(innerHeight - edge * 2, downward ? below : above)) + 'px';
      popup.style.left = Math.max(edge, Math.min(anchor.right - width, innerWidth - width - edge)) + 'px';
      popup.style.top = Math.max(edge, Math.min(downward ? anchor.bottom + gap : anchor.top - gap - popup.offsetHeight, innerHeight - popup.offsetHeight - edge)) + 'px';
    }
    function cleanup() {
      opened = false;panel.hidden = true;button.setAttribute('aria-expanded', 'false');
      window.removeEventListener('resize', position);document.removeEventListener('scroll', position, true);
      if (active === api) active = null;
    }
    function close(restoreFocus = false) {
      if (!opened) return;
      popup.hidePopover();cleanup();
      if (restoreFocus && button.isConnected) button.focus({preventScroll:true});
    }
    function open() {
      if (button.disabled || onOpen?.() === false) return;
      active?.close();panel.hidden = false;opened = true;active = api;
      popup.showPopover();position();button.setAttribute('aria-expanded', 'true');
      popup.querySelector('input:not(:disabled), select:not(:disabled), button:not(:disabled)')?.focus({preventScroll:true});
      window.addEventListener('resize', position);document.addEventListener('scroll', position, true);
    }
    function toggle() {if (opened) close(true);else open();}
    function dismiss() {close();}
    button.addEventListener('click', toggle);
    popup.addEventListener('toggle', event => {if (event.newState === 'closed' && opened) cleanup();});
    popup.addEventListener('keydown', event => {if (event.key === 'Escape') {event.preventDefault();event.stopPropagation();close(true);}});
    popup.addEventListener('focusout', event => {
      // During a native focus transition activeElement may still be body.
      // The destination distinguishes a real exit from moving between fields.
      if (event.relatedTarget && !popup.contains(event.relatedTarget) && event.relatedTarget !== button) close();
    });
    const observer = new MutationObserver(() => {
      if (tableRegion) controls.hidden = tableRegion.hidden;
      if (panel.hidden || owner && !owner.classList.contains('active') || tableRegion?.hidden) close();
    });
    observer.observe(panel, {attributes:true,attributeFilter:['hidden']});
    if (owner) observer.observe(owner, {attributes:true,attributeFilter:['class']});
    if (tableRegion) observer.observe(tableRegion, {attributes:true,attributeFilter:['hidden']});
    window.addEventListener('hashchange', dismiss);window.addEventListener('popstate', dismiss);
    window.addEventListener('workforce:sectionchange', dismiss);
    const api = {open, close, destroy() {
      close();observer.disconnect();button.removeEventListener('click',toggle);
      window.removeEventListener('hashchange',dismiss);window.removeEventListener('popstate',dismiss);
      window.removeEventListener('workforce:sectionchange',dismiss);home.replaceWith(panel);popup.remove();
    }};
    return api;
  }};
})();
