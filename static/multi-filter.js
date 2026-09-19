(() => {
  'use strict';
  const prefix = '__multi_filter_v1__:';
  const values = value => Array.isArray(value) ? value : !value ? [] : String(value).startsWith(prefix) ? JSON.parse(value.slice(prefix.length)) : [String(value)];
  const pack = list => !list.length ? '' : list.length === 1 && !list[0].startsWith(prefix) ? list[0] : prefix + JSON.stringify(list);
  const matches = (selection, value) => !values(selection).length || values(selection).includes(String(value ?? ''));
  const widgets = new WeakMap();
  let closeOpenFilter = null;
  let openRequest = 0;
  const node = (tag, text) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; return el; };
  function get(select) { return pack([...select.selectedOptions].map(o => o.value).filter(v => v !== (select.dataset.multiAll || ''))); }
  function set(select, selection) {
    const chosen = values(selection);
    widgets.get(select)?.materialize?.(chosen);
    for (const option of select.options) option.selected = chosen.includes(option.value) || (!chosen.length && option.value === (select.dataset.multiAll || ''));
    widgets.get(select)?.sync();
  }
  function params(query, key, selection, transform = value => value) {
    query.delete(key); values(selection).forEach(value => query.append(key, transform(value))); return query;
  }
  function enable(select, all = '', settings = {}) {
    if (!select || widgets.has(select)) return select;
    const selected = select.value;
    select.dataset.multiAll = all; select.multiple = true; select.classList.add('multi-filter-native');
    const button = node('button'); button.type = 'button'; button.className = 'multi-filter-button'; button.setAttribute('aria-haspopup', 'dialog'); button.setAttribute('aria-expanded', 'false');
    select.after(button);
    const title = select.getAttribute('aria-label') || [...(select.closest('label')?.childNodes || [])].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim() || 'Фильтр';
    function sync() {
      const chosen = [...select.selectedOptions].filter(o => o.value !== all);
      button.textContent = settings.caption?.(chosen) ?? (chosen.length ? chosen.length === 1 ? chosen[0].textContent : 'Выбрано: ' + chosen.length : [...select.options].find(o => o.value === all)?.textContent || 'Все значения');
      button.title = title + ': ' + (chosen.length ? chosen.map(o => o.textContent).join(', ') : 'Все значения');
      button.setAttribute('aria-label', title + ': ' + button.textContent);
      button.disabled = select.matches(':disabled');
    }
    function materialize(chosen) {
      if (!settings.options) return;
      const labels = new Map(settings.options().map(option => [String(option.value), option.textContent]));
      const entries = chosen.map(value => ({value:String(value),textContent:labels.get(String(value)) ?? String(value)}));
      const current = [...select.options].filter(option => option.value !== all);
      if (current.length === entries.length && current.every((option,index) => option.value === entries[index].value && option.textContent === entries[index].textContent)) return;
      select.replaceChildren(select.options[0], ...entries.map(item => {
        const option=node('option',item.textContent);option.value=item.value;return option;
      }));
    }
    widgets.set(select, {sync,materialize}); set(select, selected === all ? '' : selected);
    new MutationObserver(sync).observe(select, {childList:true,subtree:true,attributes:true});
    select.addEventListener('change',sync);
    let pendingRequest = 0;
    button.addEventListener('click', async event => {
      event.preventDefault();
      if (select.matches(':disabled')) return;
      const request = ++openRequest; pendingRequest = request;
      let allowed = settings.beforeOpen?.();
      if (allowed && typeof allowed.then === 'function') {
        button.setAttribute('aria-busy', 'true');
        const cancel = event => { if (event.key === 'Escape' && request === openRequest) openRequest++; };
        document.addEventListener('keydown', cancel);
        try { allowed = await allowed; }
        finally { document.removeEventListener('keydown', cancel); if (request === pendingRequest) button.removeAttribute('aria-busy'); }
      }
      if (request === openRequest && allowed !== false && button.isConnected && button.getClientRects().length && !select.matches(':disabled')) open(select, title, button, settings);
    });
    return select;
  }
  function open(select, title, button, settings = {}) {
    closeOpenFilter?.();
    const dialog = node('div'); dialog.className = 'multi-filter-dialog'; dialog.setAttribute('popover', 'auto'); dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-label', title);
    const search = node('input'); search.type = 'search'; search.placeholder = settings.placeholder || 'Найти значение'; search.setAttribute('aria-label', 'Поиск значений');
    function position() {
      const anchor = button.getBoundingClientRect(), gap = 6, edge = 12;
      const width = Math.min(Math.max(anchor.width, 340), 420, window.innerWidth - edge * 2);
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
    const options = (settings.options ? settings.options() : [...select.options]).filter(o => o.value !== (select.dataset.multiAll || '') && !o.disabled);
    const selected = new Set(values(get(select)));
    const list = node('div'); list.className = 'multi-filter-options';
    const count = node('p'); count.setAttribute('role','status');
    const limit = settings.limit ?? 100;
    const matchingOptions = () => {
      const query = search.value.toLocaleLowerCase('ru').replace(/ё/g,'е');
      const words = settings.selectedOnly ? query.split(/\s+/).filter(Boolean) : query ? [query] : [];
      return options.filter(o => words.length ? words.every(word => o.textContent.toLocaleLowerCase('ru').replace(/ё/g,'е').includes(word)) : !settings.selectedOnly || selected.has(o.value));
    };
    function render() {
      const found = matchingOptions();
      const visible = settings.renderLimit ? found.slice(0, settings.renderLimit) : found;
      list.replaceChildren(...visible.map(option => {
        const label = node('label'), check = node('input'); check.type = 'checkbox'; check.checked = selected.has(option.value);
        check.addEventListener('change',()=>{if(check.checked)selected.add(option.value);else selected.delete(option.value);summary();});
        label.append(check,node('span',option.textContent)); return label;
      }));
      if (visible.length < found.length) list.append(node('p', `Показано ${visible.length} из ${found.length}. Уточните поиск.`));
      if (!list.childElementCount) list.append(node('p', search.value ? 'Совпадений нет.' : settings.emptyLabel || 'Нет значений.'));
      summary();
    }
    function summary() { count.textContent = selected.size > limit ? 'Можно выбрать до ' + limit + ' значений. Для всех значений нажмите «Сбросить».' : selected.size ? 'Выбрано: ' + selected.size : settings.emptyLabel || 'Все значения'; if (apply) apply.disabled=selected.size>limit; }
    let apply;
    const actions = node('div'); actions.className = 'multi-filter-actions';
    const action = (label, run) => { const button=node('button',label);button.type='button';button.className='secondary-button';button.addEventListener('click',run);actions.append(button);return button; };
    action('Выбрать найденные',()=>{matchingOptions().forEach(o=>selected.add(o.value));render();});
    if (settings.selectedOnly) action('Снять все',()=>{selected.clear();render();});
    action(settings.resetLabel || 'Сбросить',()=>{selected.clear();settings.resetValues?.().forEach(value=>selected.add(String(value)));render();});
    action('Отмена',()=>{close();button.focus();});
    apply=action('Применить',()=>{
      if (settings.onApply?.([...selected]) === false) return;
      set(select,[...selected]);select.dispatchEvent(new Event('change',{bubbles:true}));close();button.focus();
    });apply.className='primary-button';
    search.addEventListener('input',()=>{render();position();});
    dialog.addEventListener('toggle',event=>{if(event.newState==='closed')cleanup();});
    dialog.append(search,...(settings.hint ? [node('p',settings.hint)] : []),count,list,actions);(select.closest('dialog[open]') || document.body).append(dialog);render();dialog.showPopover();position();search.focus();
    button.setAttribute('aria-expanded', 'true'); closeOpenFilter = close;
    window.addEventListener('resize', position); document.addEventListener('scroll', position, true);
  }
  window.MultiFilter = {values,pack,matches,get,set,params,enable};
})();
