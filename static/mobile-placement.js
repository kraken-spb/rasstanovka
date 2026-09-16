(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const mobile = matchMedia('(max-width: 900px)');
  const nav = document.querySelector('.mobile-nav');
  const view = $('view-staffing');
  const readOnly = document.querySelector('.app-shell')?.dataset.role === 'hr_viewer';
  const moved = [];
  let picking = null, active = false, latest = {count: 0, hidden: 0, filters: 0};
  const el = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    Object.entries(props).forEach(([key, value]) => {
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key in node) node[key] = value;
      else node.setAttribute(key, value);
    });
    children.flat().forEach(child => { if (child != null) node.append(child); });
    return node;
  };
  const button = (label, onClick, props = {}) => el('button', {type: 'button', className: 'secondary-button', onclick: onClick, ...props}, label);
  function move(node, parent) {
    if (!node) return;
    const marker = document.createComment('mobile layout position');
    node.before(marker); moved.push({node, marker}); parent.append(node);
  }
  function disclosure(id, label, className) {
    return el('details', {id, className}, el('summary', {}, label), el('div', {className: 'mobile-panel-content'}));
  }
  const filters = disclosure('mobile-staffing-filters', 'Фильтры', 'mobile-disclosure');
  const tools = disclosure('mobile-staffing-tools', 'Ещё инструменты', 'mobile-disclosure');
  const selected = disclosure('mobile-staffing-selected', 'Выбрано: 0 · Действия', 'mobile-selection-dock');
  const hiddenNote = el('p', {className: 'table-note'});
  const quick = el('div', {className: 'mobile-staffing-quick'});
  const search = el('div', {className: 'mobile-staffing-search'});
  const moreDialog = el('dialog', {className: 'mobile-navigation-dialog', 'aria-labelledby': 'mobile-navigation-title'},
    el('div', {className: 'mobile-dialog-heading'}, el('h2', {id: 'mobile-navigation-title'}, 'Разделы'),
      button('Закрыть', () => moreDialog.close())), el('div', {className: 'mobile-navigation-items'}));
  const more = button('Ещё', () => moreDialog.showModal(), {'aria-haspopup': 'dialog', 'aria-label': 'Ещё разделы'});
  document.body.append(moreDialog);
  moreDialog.addEventListener('click', event => { if (event.target.closest('[data-view]')) moreDialog.close(); });
  const syncNav = () => more.classList.toggle('active', !!moreDialog.querySelector('[data-view].active'));
  new MutationObserver(syncNav).observe(moreDialog, {subtree: true, attributes: true, attributeFilter: ['class']});
  function syncSelection(detail = latest) {
    latest = detail;
    selected.hidden = !active || !detail.count || !view?.classList.contains('active');
    selected.querySelector('summary').textContent = 'Выбрано: ' + detail.count + (readOnly ? '' : ' · Действия');
    hiddenNote.textContent = readOnly ? (detail.hidden ? 'Скрыты текущими фильтрами: ' + detail.hidden + '.' : 'Выделенные сотрудники доступны для просмотра.') : detail.hidden ? 'Скрыты текущими фильтрами: ' + detail.hidden + '. Действия затронут всех выбранных.' : 'Действия применяются только к выбранным сотрудникам.';
    filters.querySelector('summary').textContent = detail.filters ? 'Фильтры · ' + detail.filters : 'Фильтры';
    document.body.classList.toggle('mobile-has-selection', !selected.hidden);
    if (!detail.count) selected.open = false;
  }
  document.addEventListener('staffing-selection-change', event => syncSelection(event.detail));
  if (view) new MutationObserver(() => syncSelection()).observe(view, {attributes: true, attributeFilter: ['class']});
  function enable() {
    if (active) return;
    active = true; document.body.classList.add('mobile-enhanced');
    if (nav) {
      const primary = new Set(['staffing', 'employees', 'dashboard']);
      [...nav.querySelectorAll('[data-view]')].filter(node => !primary.has(node.dataset.view))
        .forEach(node => move(node, moreDialog.querySelector('.mobile-navigation-items')));
      if (moreDialog.querySelector('[data-view]')) nav.append(more);
      syncNav();
    }
    if (!view) return;
    const toolbar = view.querySelector('.staffing-toolbar');
    toolbar.before(search, quick, filters, tools);
    view.append(selected);
    move($('staffing-search')?.closest('label'), search);
    move($('staffing-unassigned')?.closest('label'), quick);
    move($('staffing-refresh'), quick);
    for (const id of ['grouping', 'category', 'department', 'employer', 'contractor', 'pps', 'author', 'freshness', 'regex']) {
      move($('staffing-' + id)?.closest('label'), filters.lastElementChild);
    }
    move($('staffing-reset-filters'), filters.lastElementChild);
    move($('staffing-freshness-help'), filters.lastElementChild);
    for (const id of ['collapse', 'expand', 'undo', 'redo', 'columns-toggle']) move($('staffing-' + id), tools.lastElementChild);
    move(view.querySelector('.staffing-transfers'), tools.lastElementChild);
    for (const id of ['transfer-selected', 'transfer-tomorrow', 'category-selected', 'employer-selected', 'work-selected', 'clear-selected']) {
      move($('staffing-' + id), selected.lastElementChild);
    }
    selected.lastElementChild.prepend(hiddenNote);
    selected.lastElementChild.append(
      button('К выбранным сотрудникам', () => {
        selected.open = false;
        const row = view.querySelector('.staffing-row-selected');
        const group = view.querySelector('.staffing-select-crew:checked, .staffing-select-crew:indeterminate');
        (row || group?.closest('tbody'))?.scrollIntoView({block: 'start', behavior: 'smooth'});
      }), button('Снять выделение', () => window.staffingScreen.clearSelection()));
    selected.lastElementChild.addEventListener('click', closeActions);
    syncSelection(window.staffingScreen.mobileSelection());
  }
  function closeActions(event) { if (event.target.closest('button')) selected.open = false; }
  function disable() {
    if (!active) return;
    picking?.close(); moreDialog.close(); more.remove();
    [...moved].reverse().forEach(({node, marker}) => { marker.replaceWith(node); }); moved.length = 0;
    for (const node of [filters, tools, selected, quick, search]) node.remove();
    selected.lastElementChild.replaceChildren(); selected.lastElementChild.removeEventListener('click', closeActions);
    active = false; document.body.classList.remove('mobile-enhanced', 'mobile-has-selection', 'mobile-keyboard');
  }
  mobile.addEventListener('change', () => mobile.matches ? enable() : disable());
  if (mobile.matches) enable();
  window.visualViewport?.addEventListener('resize', () => {
    document.body.classList.toggle('mobile-keyboard', active && innerHeight - visualViewport.height > 140);
  });

  function pickLocation({name, objects, sites, currentObject, currentSite, onChoose}) {
    if (picking) return;
    let groupId = currentObject || null;
    const groups = new Map(objects.map(item => [item.id, item]));
    const searchInput = el('input', {type: 'search', placeholder: 'Название группы или подобъекта', 'aria-label': 'Поиск места работы', autocomplete: 'off'});
    const list = el('div', {className: 'mobile-location-results'});
    const note = el('p', {className: 'table-note', role: 'status'});
    const back = button('Все группы', () => { groupId = null; searchInput.value = ''; render(); });
    const choose = site => { dialog.close(); onChoose(site); };
    const dialog = el('dialog', {className: 'mobile-location-dialog', 'aria-labelledby': 'mobile-location-title'},
      el('div', {className: 'mobile-dialog-heading'}, el('h2', {id: 'mobile-location-title'}, 'Место работы'), button('Закрыть', () => dialog.close())),
      el('p', {className: 'mobile-location-worker'}, name), searchInput, back, note, list,
      currentSite ? button('Снять назначение', () => choose(null), {className: 'text-button error-text'}) : null);
    function render() {
      const query = searchInput.value.toLocaleLowerCase('ru').replace(/ё/g, 'е').trim();
      const match = text => query.split(/\s+/).every(word => text.toLocaleLowerCase('ru').replace(/ё/g, 'е').includes(word));
      back.hidden = !groupId;
      searchInput.placeholder = groupId ? 'Поиск подобъекта в этой группе' : 'Название группы или подобъекта';
      if (query || groupId) {
        const results = sites.filter(site => (!groupId || site.object_id === groupId) && match((groups.get(site.object_id)?.label || '') + ' ' + site.name));
        note.textContent = (groupId ? groups.get(groupId)?.label + '. ' : '') + 'Найдено: ' + results.length + '. Выбор подобъекта сохранит назначение.';
        list.replaceChildren(...results.slice(0, 100).map(site => button('', () => choose(site.id), {className: 'mobile-location-option'})));
        [...list.children].forEach((node, i) => node.append(el('strong', {}, results[i].name), el('small', {}, groups.get(results[i].object_id)?.label || '')));
        if (results.length > 100) list.append(el('p', {}, 'Уточните поиск — показаны первые 100 результатов.'));
        if (!results.length) list.append(el('p', {}, 'Ничего не найдено. Измените запрос или вернитесь ко всем группам.'));
      } else {
        note.textContent = 'Выберите группу или найдите подобъект по названию.';
        list.replaceChildren(...objects.map(group => button(group.label, () => { groupId = group.id; render(); searchInput.focus(); }, {className: 'mobile-location-option'})));
      }
      list.scrollTop = 0;
    }
    searchInput.addEventListener('input', render);
    dialog.addEventListener('close', () => { picking = null; dialog.remove(); });
    picking = dialog; document.body.append(dialog); render(); dialog.showModal(); searchInput.focus();
  }
  window.mobilePlacement = {pickLocation, isPicking: () => !!picking};
})();
