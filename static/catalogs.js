(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('view-catalogs')) return;
  let section = 'categories';
  const canLeave = () => window.smuScreen.canLeave() && window.categoriesScreen.canLeave() && window.contractorsScreen.canLeave() && window.locationsScreen.canLeave();
  async function show(next = section) {
    if (!canLeave()) return;
    section = next;
    document.querySelectorAll('[data-catalog]').forEach(button => {
      const selected = button.dataset.catalog === section;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
    $('view-categories').hidden = section !== 'categories';
    $('view-categories').classList.toggle('active', section === 'categories');
    $('view-smu').hidden = section !== 'smu';
    $('view-smu').classList.toggle('active', section === 'smu');
    $('view-contractors').hidden = section !== 'contractors';
    $('view-contractors').classList.toggle('active', section === 'contractors');
    $('location-catalog').hidden = !['stages', 'groups', 'subobjects'].includes(section);
    $('catalog-stages').hidden = section !== 'stages';
    $('catalog-groups').hidden = section !== 'groups';
    $('catalog-subobjects').hidden = section !== 'subobjects';
    $('catalog-location-title').textContent = {stages: 'Этапы', groups: 'Группы подобъектов', subobjects: 'Подобъекты'}[section] || '';
    window.locationsScreen.setSection(section);
    if (section === 'categories') await window.categoriesScreen.load();
    else if (section === 'smu') await window.smuScreen.load();
    else if (section === 'contractors') await window.contractorsScreen.load();
    else await window.locationsScreen.load();
  }
  document.querySelectorAll('[data-catalog]').forEach(button => button.addEventListener('click', () => show(button.dataset.catalog)));
  window.catalogsScreen = {load: () => show(), canLeave};
})();
