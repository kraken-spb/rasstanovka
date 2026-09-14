(() => {
  'use strict';
  const labels = {assigned: 'Расставлены', unassigned: 'Не расставлены', absent: 'Неявка', total: 'Всего'};
  const counts = () => ({assigned: 0, unassigned: 0, absent: 0, total: 0});
  function build(data) {
    const categories = new Map(), companies = new Map(), overall = {...counts(), people: [], companies};
    for (const group of data.groups) {
      if (!categories.has(group.category)) categories.set(group.category, {...counts(), category: group.category, people: [], companies: new Map()});
      const category = categories.get(group.category);
      for (const target of [category, overall]) {
        for (const key of Object.keys(labels)) target[key] += group[key];
        target.people.push(...group.people);
        for (const [company, values] of Object.entries(group.companies)) {
          if (!target.companies.has(company)) target.companies.set(company, counts());
          for (const key of Object.keys(labels)) target.companies.get(company)[key] += values[key];
        }
      }
    }
    return {categories: [...categories.values()].sort((a, b) => a.category.localeCompare(b.category, 'ru', {numeric: true})), overall};
  }
  function render(data, target, peopleList) {
    const report = build(data);
    const el = (tag, attrs = {}, ...children) => {
      const node = document.createElement(tag); Object.assign(node, attrs); node.append(...children); return node;
    };
    target.replaceChildren(el('h2', {}, 'Расстановка по категориям ГДТЛР'),
      el('p', {className: 'table-note'}, 'На ' + data.date.split('-').reverse().join('.') + '. Категории объединены по выбранным ППС. Нажмите число, чтобы увидеть сотрудников. Неявка учитывается отдельно.'));
    if (!report.categories.length) { target.append(el('p', {className: 'empty-state'}, 'По выбранным условиям сотрудников нет.')); return; }
    const scroller = el('div', {className: 'category-placement-scroll', tabIndex: 0});
    scroller.setAttribute('role', 'region'); scroller.setAttribute('aria-label', 'Расставленные и нерасставленные по категориям ГДТЛР и подрядчикам');
    const table = el('table', {className: 'category-placement-table'}), head = el('thead'), top = el('tr'), statuses = el('tr');
    top.append(el('th', {scope: 'col', rowSpan: 2}, 'Категория ГДТЛР'));
    const columns = [undefined, ...data.contractors];
    const companyLabel = company => company === undefined ? 'Общий итог' : company || 'Подрядчик не указан';
    for (const company of columns) {
      top.append(el('th', {scope: 'colgroup', colSpan: 4}, companyLabel(company)));
      for (const [key, label] of Object.entries(labels)) statuses.append(el('th', {scope: 'col', className: key}, label));
    }
    head.append(top, statuses); table.append(head);
    const body = el('tbody'), details = el('section', {className: 'placement-report-selected', hidden: true, tabIndex: -1});
    function row(group, title, total = false) {
      const tr = el('tr', {className: total ? 'category-placement-total' : ''}, el('th', {scope: 'row'}, title));
      for (const company of columns) for (const [key, label] of Object.entries(labels)) {
        const count = company === undefined ? group[key] : group.companies.get(company)?.[key] || 0;
        const td = el('td', {className: key});
        if (count) {
          const caption = [title, companyLabel(company), label].join(' · ');
          const button = el('button', {type: 'button', className: 'report-fact-link'}, String(count));
          button.setAttribute('aria-label', caption + ': ' + count);
          button.addEventListener('click', () => {
            const people = group.people.filter(p => (key === 'total' || p.status === key) && (company === undefined || p.contractor === company));
            const close = el('button', {type: 'button', className: 'secondary-button'}, 'Закрыть список');
            close.addEventListener('click', () => { details.hidden = true; button.focus({preventScroll: true}); });
            details.replaceChildren(el('h3', {}, caption + ' (' + people.length + ')'), close, peopleList(people));
            details.hidden = false; details.focus({preventScroll: true}); details.scrollIntoView({block: 'nearest'});
          });
          td.append(button);
        } else td.textContent = '0';
        tr.append(td);
      }
      body.append(tr);
    }
    report.categories.forEach(group => row(group, group.category || 'Без категории'));
    row(report.overall, 'Итого', true);
    table.append(body); scroller.append(table); target.append(scroller, details);
  }
  window.categoryPlacement = {build, render};
})();
