(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('view-catalogs')) return;
  const view = $('view-catalogs'), rail = $('catalog-rail'), nav = $('catalog-navigation');
  const key = 'catalog-section:' + document.querySelector('.app-shell').dataset.userId;
  const error = document.createElement('p');error.className = 'error-text';error.setAttribute('role','alert');error.hidden = true;
  view.querySelector('.page-heading').after(error);
  let section = 'categories', switching = false;
  const canLeave = () => !switching && (!window.workforceCatalogs || window.workforceCatalogs.canLeave()) && window.crewCatalogScreen.canLeave() && window.smuScreen.canLeave() && window.categoriesScreen.canLeave() && window.contractorsScreen.canLeave() && window.locationsScreen.canLeave();
  const paths = {
    categories: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
    crews: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M16 3a4 4 0 0 1 0 8 M22 21v-2a4 4 0 0 0-3-4 M13 7a4 4 0 1 1-8 0a4 4 0 0 1 8 0',
    smu: 'M3 21V7h8v14 M11 3h10v18 M6 11h2 M6 15h2 M15 7h2 M15 11h2 M15 15h2 M1 21h22',
    contractors: 'M3 7h18v14H3z M8 7V3h8v4 M3 12h18 M10 12v3h4v-3',
    stages: 'M5 3v18 M5 4h14l-3 4 3 4H5', groups: 'M3 5h7l2 3h9v12H3z',
    subobjects: 'M12 22s8-8 8-13a8 8 0 0 0-16 0c0 5 8 13 8 13z M15 9a3 3 0 1 1-6 0a3 3 0 0 1 6 0',
    organization: 'M3 21V3h13v18 M16 10h5v11 M7 7h5 M7 11h5 M7 15h5 M1 21h22',
    citizenship: 'M22 12a10 10 0 1 1-20 0a10 10 0 0 1 20 0 M2 12h20 M12 2c-6 6-6 14 0 20 M12 2c6 6 6 14 0 20',
    profession: 'M3 8h18v13H3z M8 8V4h8v4 M3 13h18 M10 13v3h4v-3',
    travelpoint: 'M12 22s8-8 8-13a8 8 0 0 0-16 0c0 5 8 13 8 13z M15 9a3 3 0 1 1-6 0a3 3 0 0 1 6 0',
    place: 'M3 11l9-8 9 8 M5 10v11h14V10 M9 21v-8h6v8',
    schedule: 'M3 5h18v16H3z M7 3v4 M17 3v4 M3 10h18 M7 14h2 M15 14h2 M7 18h2',
    document: 'M5 3h9l5 5v13H5z M14 3v6h5 M8 13h8 M8 17h5',
    check: 'M8 4h8v4H8z M8 6H4v15h16V6h-4 M8 14l3 3 5-6',
    project: 'M3 21V9l9-6 9 6v12 M7 21v-7h10v7 M3 9h18 M9 9V5 M15 9V5',
    employment: 'M3 5h18v14H3z M10 10a2 2 0 1 1-4 0a2 2 0 0 1 4 0 M5 16c0-4 6-4 6 0 M14 9h4 M14 13h4',
    stage: 'M12 3v9h7 M22 12a10 10 0 1 1-20 0a10 10 0 0 1 20 0',
    direction: 'M3 7h16 M15 3l4 4-4 4 M21 17H5 M9 13l-4 4 4 4',
    destination: 'M5 21V3 M5 4h14l-3 4 3 4H5 M2 21h7',
    basis: 'M3 7h18v4a2 2 0 0 0 0 4v4H3v-4a2 2 0 0 0 0-4z M15 7v3 M15 13v3',
    result: 'M3 7h15 M14 3l4 4-4 4 M21 17H6 M10 13l-4 4 4 4',
    docstate: 'M5 3h9l5 5v13H5z M14 3v6h5 M8 15l2 2 5-5',
    checkstate: 'M12 3l8 4v5c0 5-8 9-8 9s-8-4-8-9V7z M8 12l3 3 5-6',
  };
  function icon(path) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
    for (const [name,value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.6','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'})) svg.setAttribute(name,value);
    const shape = document.createElementNS(svg.namespaceURI,'path');shape.setAttribute('d',path);svg.append(shape);return svg;
  }
  function group(label) {
    const heading = document.createElement('div');heading.className = 'catalog-nav-group';
    const text = document.createElement('span');text.textContent = label;heading.append(text);return heading;
  }
  if (rail) {
    nav.prepend(group('Расстановка'));nav.append(group('Учёт персонала'));
    for (const [kind,label] of window.workforceCatalogs.kinds) {
      const button = document.createElement('button');button.type = 'button';button.dataset.catalog = 'workforce:' + kind;
      button.textContent = label;button.setAttribute('aria-pressed','false');nav.append(button);
    }
    for (const button of nav.querySelectorAll('[data-catalog]')) {
      const label = button.textContent.trim(), name = button.dataset.catalog.replace('workforce:','');
      const text = document.createElement('span');text.textContent = label;
      button.title = label;button.setAttribute('aria-label',label);button.replaceChildren(icon(paths[name]),text);
    }
    $('catalog-rail-toggle').prepend(icon('M9 5l7 7-7 7'));
  }
  const buttons = [...nav.querySelectorAll('[data-catalog]')];
  try {const saved = localStorage.getItem(key);if (buttons.some(button => button.dataset.catalog === saved)) section = saved;} catch (_) {}
  function filter() {
    if (!rail) return;
    const query = $('catalog-rail-search').value.toLocaleLowerCase('ru').replace(/ё/g,'е').trim();
    for (const button of buttons) button.hidden = !button.title.toLocaleLowerCase('ru').replace(/ё/g,'е').includes(query);
    for (const heading of nav.querySelectorAll('.catalog-nav-group')) {
      let item = heading.nextElementSibling, visible = false;
      while (item && !item.classList.contains('catalog-nav-group')) {visible ||= !item.hidden;item = item.nextElementSibling;}
      heading.hidden = !visible;
    }
    $('catalog-search-empty').hidden = buttons.some(button => !button.hidden);
  }
  function expand(open, focus = false) {
    if (!rail) return;
    view.classList.toggle('catalog-rail-open',open);
    $('catalog-rail-toggle').setAttribute('aria-expanded',String(open));
    $('catalog-rail-toggle').setAttribute('aria-label',open ? 'Свернуть справочники' : 'Развернуть справочники');
    $('catalog-rail-toggle').title = open ? 'Свернуть справочники' : 'Развернуть справочники';
    $('catalog-rail-backdrop').hidden = !open;
    if (!open) {$('catalog-rail-search').value = '';filter();}
    if (focus) (open ? $('catalog-rail-search') : $('catalog-rail-toggle')).focus();
  }
  function select(next) {
    section = next;
    const workforce = section.startsWith('workforce:');
    if ($('catalog-permission-note')) $('catalog-permission-note').hidden = section === 'crews' || workforce;
    if ($('workforce-catalog-panel')) $('workforce-catalog-panel').hidden = !workforce;
    for (const button of buttons) {
      const selected = button.dataset.catalog === section;
      button.classList.toggle('active',selected);button.setAttribute('aria-pressed',String(selected));
      if (selected) button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
    }
    for (const [id,name] of [['view-crew-catalog','crews'],['view-categories','categories'],['view-smu','smu'],['view-contractors','contractors']]) {
      $(id).hidden = section !== name;$(id).classList.toggle('active',section === name);
    }
    $('location-catalog').hidden = !['stages','groups','subobjects'].includes(section);
    for (const name of ['stages','groups','subobjects']) $('catalog-' + name).hidden = section !== name;
    $('catalog-location-title').textContent = {stages:'Этапы',groups:'Группы подобъектов',subobjects:'Подобъекты'}[section] || '';
    window.locationsScreen.setSection(section);
  }
  async function show(next = section, check = true) {
    if (switching || !buttons.some(button => button.dataset.catalog === next) || (check && !canLeave())) return false;
    switching = true;nav.setAttribute('aria-busy','true');buttons.forEach(button => {button.disabled = true;});
    error.hidden = true;
    try {
      window.workforceCatalogs?.discard();
      if (next.startsWith('workforce:') && !(await window.workforceCatalogs.load(next.slice(10)))) {
        error.textContent = $('workforce-catalog-panel').querySelector('.wf-catalog-load-error')?.textContent || 'Не удалось открыть справочник. Повторите выбор.';
        error.hidden = false;return false;
      }
      select(next);
      if (section === 'categories') await window.categoriesScreen.load();
      else if (section === 'crews') await window.crewCatalogScreen.load();
      else if (section === 'smu') await window.smuScreen.load();
      else if (section === 'contractors') await window.contractorsScreen.load();
      else if (!section.startsWith('workforce:')) await window.locationsScreen.load();
      try {localStorage.setItem(key,section);} catch (_) {}
      expand(false);return true;
    } catch (failure) {error.textContent = failure.message;error.hidden = false;return false;
    } finally {switching = false;nav.removeAttribute('aria-busy');buttons.forEach(button => {button.disabled = false;});}
  }
  buttons.forEach(button => button.addEventListener('click',() => show(button.dataset.catalog)));
  if (rail) {
    $('catalog-rail-toggle').addEventListener('click',() => expand(!view.classList.contains('catalog-rail-open'),true));
    $('catalog-rail-backdrop').addEventListener('click',() => expand(false,true));
    $('catalog-rail-search').addEventListener('input',filter);
    rail.addEventListener('keydown',event => {
      if (event.key === 'Escape' && view.classList.contains('catalog-rail-open')) {event.preventDefault();expand(false,true);return;}
      if (!event.target.matches('[data-catalog]')) return;
      const shown = buttons.filter(button => !button.hidden && !button.disabled), index = shown.indexOf(event.target);
      const next = {ArrowDown:(index+1)%shown.length,ArrowUp:(index+shown.length-1)%shown.length,Home:0,End:shown.length-1}[event.key];
      if (next !== undefined) {event.preventDefault();shown[next]?.focus();}
    });
    new MutationObserver(() => {if (!view.classList.contains('active') && view.classList.contains('catalog-rail-open')) expand(false);}).observe(view,{attributes:true,attributeFilter:['class']});
  }
  window.catalogsScreen = {load:() => show(section,false),canLeave};
})();
