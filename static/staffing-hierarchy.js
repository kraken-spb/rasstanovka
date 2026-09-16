((root) => {
  'use strict';
  const fields = {itr: 'Линейный ИТР', crew: 'Бригада', shift: 'Смена', department: 'СМУ',
    pps: 'ППС', category: 'Категория ГДЛР', employer: 'Работодатель', contractor: 'Подрядчик'};
  const defaults = ['itr', 'crew'];
  function valid(levels) {
    return Array.isArray(levels) && levels.length > 0 && levels.length <= Object.keys(fields).length &&
      levels.every(key => Object.hasOwn(fields, key)) && new Set(levels).size === levels.length;
  }
  function describe(levels) { return levels.map(key => fields[key]).join(' → '); }
  function build(rows, levels, crews = []) {
    if (!valid(levels)) throw new Error('Некорректные уровни группировки');
    const crewNames = new Map(crews.map(crew => [crew.id, crew.name]));
    const roots = [], byId = new Map(), paths = new Map(), seen = new Set();
    function value(row, field) {
      if (field === 'itr') return [row.itr_group_key || null, row.itr_group_label || 'Линейный ИТР не указан'];
      if (field === 'crew') return [row.crew_id, crewNames.get(row.crew_id) || 'Без бригады'];
      if (field === 'shift') {
        const shift = row.employee_shift === 'Ночная смена' ? '2 смена' : row.employee_shift;
        return [shift || null, shift === '1 смена' ? 'День' : shift === '2 смена' ? 'Ночь' : 'Не определена'];
      }
      return [row[field] || null, row[field] || 'Не указано'];
    }
    for (const row of rows) {
      if (seen.has(row.id)) continue;
      seen.add(row.id);
      const identity = [], ids = [];
      let children = roots, parent = null;
      for (const [depth, field] of levels.entries()) {
        const [key, label] = value(row, field);
        identity.push([field, key]);
        const id = 'hier-' + encodeURIComponent(JSON.stringify(identity));
        let node = byId.get(id);
        if (!node) {
          node = {id, name: fields[field] + ': ' + label, label, field, depth,
            parentId: parent?.id || null, ancestorIds: [...ids], children: [], rows: [], itr: true, hierarchical: true};
          children.push(node); byId.set(id, node);
        }
        node.rows.push(row); ids.push(id); parent = node; children = node.children;
      }
      paths.set(row.id, ids);
    }
    const nodes = [], compare = new Intl.Collator('ru', {numeric: true});
    function visit(children) {
      children.sort((a,b) => compare.compare(a.label,b.label) || a.id.localeCompare(b.id));
      for (const node of children) { nodes.push(node); visit(node.children); }
    }
    visit(roots);
    return {roots, nodes, byId, paths};
  }
  function openEditor(levels, apply, returnFocus) {
    let selected = [...levels];
    const el = (tag, attrs = {}, ...children) => {
      const node = document.createElement(tag);
      for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
      node.append(...children); return node;
    };
    const list = el('ol', {class: 'staffing-hierarchy-levels'});
    const available = el('select', {'aria-label': 'Добавить уровень'});
    const add = el('button', {type: 'button', class: 'secondary-button'}, 'Добавить уровень');
    const save = el('button', {type: 'button', class: 'primary-button'}, 'Применить группировку');
    const cancel = el('button', {type: 'button', class: 'secondary-button'}, 'Отмена');
    const reset = el('button', {type: 'button', class: 'text-button'}, 'ИТР → бригада');
    const preview = el('p', {class: 'staffing-hierarchy-preview', role: 'status'});
    const dialog = el('dialog', {class: 'staffing-hierarchy-dialog', 'aria-labelledby': 'staffing-hierarchy-title'},
      el('h2', {id: 'staffing-hierarchy-title'}, 'Иерархия группировки'),
      el('p', {}, 'Верхний уровень — первый в списке. Меняйте порядок стрелками, добавляйте или убирайте уровни.'),
      list, el('div', {class: 'staffing-hierarchy-add'}, available, add), preview,
      el('div', {class: 'staffing-hierarchy-actions'}, reset, cancel, save));
    function close() { dialog.close(); dialog.remove(); returnFocus?.focus(); }
    function paint(focusKey, action) {
      list.replaceChildren(...selected.map((key, index) => {
        const item = el('li', {'data-level': key}, el('span', {}, fields[key]));
        for (const [kind, text, label] of [['up','↑','Выше'], ['down','↓','Ниже'], ['remove','×','Убрать']]) {
          const button = el('button', {type: 'button', class: 'secondary-button', 'data-action': kind,
            'aria-label': label + ': ' + fields[key]}, text);
          button.disabled = kind === 'up' ? index === 0 : kind === 'down' ? index === selected.length - 1 : false;
          button.onclick = () => {
            if (kind === 'remove') selected.splice(index,1);
            else { const target = index + (kind === 'up' ? -1 : 1); [selected[index],selected[target]] = [selected[target],selected[index]]; }
            paint(kind === 'remove' ? selected[Math.min(index,selected.length-1)] : key, kind);
          };
          item.append(button);
        }
        return item;
      }));
      available.replaceChildren(...Object.entries(fields).filter(([key]) => !selected.includes(key))
        .map(([key,label]) => el('option', {value: key}, label)));
      add.disabled = available.disabled = selected.length === Object.keys(fields).length;
      save.disabled = !selected.length;
      preview.textContent = selected.length ? describe(selected) + ' → сотрудники' : 'Добавьте хотя бы один уровень.';
      if (focusKey) {
        const target = list.querySelector('[data-level="' + focusKey + '"] [data-action="' + action + '"]');
        if (target && !target.disabled) target.focus(); else list.querySelector('[data-level="' + focusKey + '"] button:not(:disabled)')?.focus();
      }
    }
    add.onclick = () => { if (available.value) { const key = available.value; selected.push(key); paint(key,'remove'); } };
    reset.onclick = () => { selected = [...defaults]; paint(); };
    cancel.onclick = close;
    save.onclick = () => { if (valid(selected)) { apply([...selected]); close(); } };
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    paint(); document.body.append(dialog); dialog.showModal();
  }
  const api = {fields, defaults, valid, describe, build, openEditor};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.StaffingHierarchy = api;
})(globalThis);
