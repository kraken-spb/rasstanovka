(() => {
  'use strict';
  const prefix = '__multi_filter_v1__:';
  const values = value => Array.isArray(value) ? value : !value ? [] : String(value).startsWith(prefix) ? JSON.parse(value.slice(prefix.length)) : [String(value)];
  const pack = list => !list.length ? '' : list.length === 1 && !list[0].startsWith(prefix) ? list[0] : prefix + JSON.stringify(list);
  const matches = (selection, value) => !values(selection).length || values(selection).includes(String(value ?? ''));
  const widgets = new WeakMap();
  let closeOpenFilter = null;
  const node = (tag, text) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; return el; };
  function get(select) { return pack([...select.selectedOptions].map(o => o.value).filter(v => v !== (select.dataset.multiAll || ''))); }
  function set(select, selection) {
    const chosen = values(selection);
    for (const option of select.options) option.selected = chosen.includes(option.value) || (!chosen.length && option.value === (select.dataset.multiAll || ''));
    widgets.get(select)?.sync();
  }
  function params(query, key, selection, transform = value => value) {
    query.delete(key); values(selection).forEach(value => query.append(key, transform(value))); return query;
  }
  function enable(select, all = '') {
    if (!select || widgets.has(select)) return select;
    const selected = select.value;
    select.dataset.multiAll = all; select.multiple = true; select.classList.add('multi-filter-native');
    const button = node('button'); button.type = 'button'; button.className = 'multi-filter-button'; button.setAttribute('aria-haspopup', 'dialog'); button.setAttribute('aria-expanded', 'false');
    select.after(button);
    const title = select.getAttribute('aria-label') || [...(select.closest('label')?.childNodes || [])].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim() || 'Фильтр';
    function sync() {
      const chosen = [...select.selectedOptions].filter(o => o.value !== all);
      button.textContent = chosen.length ? chosen.length === 1 ? chosen[0].textContent : 'Выбрано: ' + chosen.length : [...select.options].find(o => o.value === all)?.textContent || 'Все значения';
      button.title = title + ': ' + (chosen.length ? chosen.map(o => o.textContent).join(', ') : 'Все значения');
      button.setAttribute('aria-label', title + ': ' + button.textContent);
      button.disabled = select.matches(':disabled');
    }
    widgets.set(select, {sync}); set(select, selected === all ? '' : selected);
    new MutationObserver(sync).observe(select, {childList:true,subtree:true,attributes:true});
    select.addEventListener('change',sync);
    button.addEventListener('click', event => { event.preventDefault(); if (!select.matches(':disabled')) open(select, title, button); });
    return select;
  }
  function open(select, title, button) {
    closeOpenFilter?.();
    const dialog = node('div'); dialog.className = 'multi-filter-dialog'; dialog.setAttribute('popover', 'auto'); dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-label', title);
    const search = node('input'); search.type = 'search'; search.placeholder = 'Найти значение'; search.setAttribute('aria-label', 'Поиск значений');
    function position() {
      const anchor = button.getBoundingClientRect(), gap = 6, edge = 12;
      const width = Math.min(Math.max(anchor.width, 300), 420, window.innerWidth - edge * 2);
      const below = window.innerHeight - anchor.bottom - gap - edge, above = anchor.top - gap - edge;
      const downward = below >= 300 || below >= above;
      dialog.style.width = width + 'px';
      dialog.style.maxHeight = Math.max(80, downward ? below : above) + 'px';
      dialog.style.left = Math.max(edge, Math.min(anchor.left, window.innerWidth - width - edge)) + 'px';
      dialog.style.top = (downward ? anchor.bottom + gap : Math.max(edge, anchor.top - gap - dialog.offsetHeight)) + 'px';
    }
    let cleaned = false;
    function close() { dialog.hidePopover(); cleanup(); }
    function cleanup() {
      if (cleaned) return;
      cleaned = true;
      window.removeEventListener('resize', position); document.removeEventListener('scroll', position, true);
      button.setAttribute('aria-expanded', 'false'); dialog.remove();
      if (closeOpenFilter === close) closeOpenFilter = null;
    }
    const options = [...select.options].filter(o => o.value !== (select.dataset.multiAll || '') && !o.disabled);
    const selected = new Set(values(get(select)));
    const list = node('div'); list.className = 'multi-filter-options';
    const count = node('p'); count.setAttribute('role','status');
    function render() {
      const query = search.value.toLocaleLowerCase('ru').replace(/ё/g,'е');
      list.replaceChildren(...options.filter(o => o.textContent.toLocaleLowerCase('ru').replace(/ё/g,'е').includes(query)).map(option => {
        const label = node('label'), check = node('input'); check.type = 'checkbox'; check.checked = selected.has(option.value);
        check.addEventListener('change',()=>{if(check.checked)selected.add(option.value);else selected.delete(option.value);summary();});
        label.append(check,node('span',option.textContent)); return label;
      })); summary();
    }
    function summary() { count.textContent = selected.size > 100 ? 'Можно выбрать до 100 значений. Для всех значений нажмите «Сбросить».' : selected.size ? 'Выбрано: ' + selected.size : 'Все значения'; if (apply) apply.disabled=selected.size>100; }
    let apply;
    const actions = node('div'); actions.className = 'multi-filter-actions';
    const action = (label, run) => { const button=node('button',label);button.type='button';button.className='secondary-button';button.addEventListener('click',run);actions.append(button);return button; };
    action('Выбрать найденные',()=>{const q=search.value.toLocaleLowerCase('ru').replace(/ё/g,'е');options.filter(o=>o.textContent.toLocaleLowerCase('ru').replace(/ё/g,'е').includes(q)).forEach(o=>selected.add(o.value));render();});
    action('Сбросить',()=>{selected.clear();render();});
    action('Отмена',()=>{close();button.focus();});
    apply=action('Применить',()=>{set(select,[...selected]);select.dispatchEvent(new Event('change',{bubbles:true}));close();button.focus();});apply.className='primary-button';
    search.addEventListener('input',()=>{render();position();});
    dialog.addEventListener('toggle',event=>{if(event.newState==='closed')cleanup();});
    dialog.append(search,count,list,actions);document.body.append(dialog);render();dialog.showPopover();position();search.focus();
    button.setAttribute('aria-expanded', 'true'); closeOpenFilter = close;
    window.addEventListener('resize', position); document.addEventListener('scroll', position, true);
  }
  window.MultiFilter = {values,pack,matches,get,set,params,enable};
})();
