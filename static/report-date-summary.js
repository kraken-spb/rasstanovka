(() => {
  'use strict';
  const normalize = value => String(value || '').toLocaleLowerCase('ru').replace(/ё/g, 'е');
  const sort = (a, b) => a.name.localeCompare(b.name, 'ru', {numeric: true});
  const sum = (target, fact) => { target.day += fact.day_count; target.night += fact.night_count; };
  const total = name => ({name, day: 0, night: 0, sites: new Set()});
  function build({data, objects, subobjects, query = ''}) {
    const objectById = new Map(objects.map(item => [item.id, item]));
    const subById = new Map(subobjects.map(item => [item.id, item]));
    const groups = new Map(), employers = new Map(), overall = total('Итого');
    const words = normalize(query).split(/\s+/).filter(Boolean);
    for (const fact of data.facts) {
      if (fact.work_date !== data.dates[0] || !(fact.day_count + fact.night_count)) continue;
      const sub = subById.get(fact.subobject_id), object = sub && objectById.get(sub.object_id);
      if (!sub || !object) throw new Error('Справочник объектов изменился. Обновите сводную таблицу.');
      if (!words.every(word => normalize(object.name + ' ' + sub.name).includes(word))) continue;
      if (!groups.has(object.id)) groups.set(object.id, {...total(object.name), id: object.id, subobjects: new Map()});
      const group = groups.get(object.id);
      if (!group.subobjects.has(sub.id)) group.subobjects.set(sub.id, {...total(sub.name), id: sub.id, employers: new Map()});
      const site = group.subobjects.get(sub.id);
      if (!site.employers.has(fact.employer)) site.employers.set(fact.employer, total(fact.employer));
      if (!employers.has(fact.employer)) employers.set(fact.employer, total(fact.employer));
      for (const target of [overall, group, site, site.employers.get(fact.employer), employers.get(fact.employer)]) {
        sum(target, fact); target.sites.add(sub.id);
      }
    }
    const finish = item => ({...item, sites: [...item.sites], count: item.day + item.night});
    return {overall: finish(overall), employers: [...employers.values()].sort(sort).map(finish),
      objects: [...groups.values()].sort(sort).map(group => ({...finish(group),
        subobjects: [...group.subobjects.values()].sort(sort).map(site => ({...finish(site),
          employers: [...site.employers.values()].sort(sort).map(finish)}))}))};
  }
  function render(container, options) {
    const {data, canDrill, openStaffing} = options;
    const report = build(options), day = data.dates[0];
    const category = data.categorySelection ? JSON.parse(data.categorySelection) : undefined;
    const element = (tag, text, className) => {
      const item = document.createElement(tag);
      if (text != null) item.textContent = text;
      if (className) item.className = className;
      return item;
    };
    const companies = report.employers.map(item => ({...item, shifts: [
      ...(item.day ? ['day'] : []), ...(item.night ? ['night'] : []), 'count']}));
    const labels = {day: '1 смена', night: '2 смена', count: 'ИТОГО'};
    const section = element('section', null, 'report-date-section');
    const toolbar = element('div', null, 'report-matrix-toolbar');
    toolbar.append(element('h2', 'Расстановка на ' + day.split('-').reverse().join('.')));
    if (canDrill) {
      const params = new URLSearchParams({date: day, shift: 'all', query: options.query || ''});
      if (category !== undefined) params.set('category', category);
      const download = element('a', 'Скачать список и сводную Excel', 'secondary-button');
      download.href = '/api/staffing/export?' + params; download.download = '';
      toolbar.append(download);
    }
    section.append(toolbar, element('p', 'Выборка: ' + (category === undefined ? 'Все категории ГДЛР' : category || 'Без категории'), 'table-note'));
    const scroller = element('div', null, 'report-matrix-scroll');
    scroller.tabIndex = 0; scroller.setAttribute('role', 'region'); scroller.setAttribute('aria-label', 'Сводная по позициям, компаниям и сменам');
    const table = element('table', null, 'report-date-table report-matrix');
    const head = element('thead'), main = element('tr'), shifts = element('tr');
    const position = element('th', 'Позиция'); position.rowSpan = 2; position.scope = 'col'; main.append(position);
    companies.forEach(company => {
      const th = element('th', company.name || 'Организация не указана');
      th.colSpan = company.shifts.length; th.scope = 'colgroup'; main.append(th);
      company.shifts.forEach(shift => { const cell = element('th', labels[shift], shift === 'count' ? 'report-company-total' : ''); cell.scope = 'col'; shifts.append(cell); });
    });
    const overallHead = element('th', 'Общий итог'); overallHead.rowSpan = 2; overallHead.scope = 'col'; main.append(overallHead);
    head.append(main, shifts); table.append(head);
    const body = element('tbody');
    function row(item, items, level) {
      const tr = element('tr', null, level), title = element('th', item.name); title.scope = 'row'; tr.append(title);
      function cell(value, shift, employer, totalClass = '') {
        const td = element('td', null, totalClass);
        if (value && canDrill) {
          const button = element('button', String(value), 'report-fact-link'); button.type = 'button';
          button.setAttribute('aria-label', item.name + ', ' + (employer || 'Все компании') + ', ' + (labels[shift] || 'Общий итог') + ': ' + value + ' чел.');
          button.addEventListener('click', () => openStaffing({sites: item.sites, date: day,
            shift: shift === 'day' ? '1 смена' : shift === 'night' ? '2 смена' : '', label: item.name, employer, category}));
          td.append(button);
        } else td.textContent = value ? String(value) : '';
        tr.append(td);
      }
      const byCompany = new Map(items.map(company => [company.name, company]));
      companies.forEach(company => company.shifts.forEach(shift => cell(byCompany.get(company.name)?.[shift] || 0, shift, company.name, shift === 'count' ? 'report-company-total' : '')));
      cell(item.count, 'count', undefined, 'report-grand-total'); body.append(tr);
    }
    report.objects.forEach(object => {
      const totals = new Map();
      object.subobjects.forEach(site => site.employers.forEach(company => {
        if (!totals.has(company.name)) totals.set(company.name, {name: company.name, day: 0, night: 0, count: 0});
        const target = totals.get(company.name); target.day += company.day; target.night += company.night; target.count += company.count;
      }));
      row(object, [...totals.values()], 'report-object');
      object.subobjects.forEach(site => row(site, site.employers, 'report-site'));
    });
    row({...report.overall, name: 'Общий итог'}, report.employers, 'report-total');
    table.append(body); scroller.append(table); section.append(scroller);
    section.append(element('p', 'Только расставленный персонал со статусом «Явка». Общий итог = день + ночь; назначения сотрудника в обе смены учитываются отдельно.', 'table-note'));
    if (!report.overall.count) { section.append(element('p', 'На выбранную дату по текущим фильтрам назначений нет.', 'report-empty')); toolbar.querySelector('a')?.remove(); }
    container.replaceChildren(section);
  }
  window.reportDateSummary = {build, render};
})();
