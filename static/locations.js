(() => {
  'use strict';
  const MF = window.MultiFilter;
  const root = document.querySelector('.app-shell');
  const panel = document.getElementById('location-catalog');
  if (!panel || !['admin', 'super_admin', 'hr_viewer'].includes(root.dataset.role)) return;
  const canEdit = root.dataset.role === 'super_admin';
  const $ = id => document.getElementById(id);
  const dialog = $('location-editor');
  const form = $('location-editor-form');
  const search = $('location-catalog-search');
  const filter = $('location-catalog-filter');
  let catalog = {stages: [], objects: [], subobjects: []}, editing = null, busy = false, loading = false;
  let section = 'groups';
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const stageFilter = element('select'); stageFilter.id = 'location-stage-filter';
  const stageLabel = element('label', 'Этап'); stageLabel.append(stageFilter);
  panel.querySelector('.location-catalog-toolbar').prepend(stageLabel);
  const stageSummary = element('div', null, 'location-catalog-toolbar');
  stageSummary.setAttribute('aria-label', 'Родительские этапы');
  panel.querySelector('.location-catalog-toolbar').before(stageSummary);
  MF.enable(filter); MF.enable(stageFilter);
  const stageEditor = element('select'); stageEditor.id = 'location-editor-stage';
  const stageEditorLabel = element('label', 'Родительский этап'); stageEditorLabel.append(stageEditor);
  $('location-editor-group-label')?.before(stageEditorLabel);
  function stageOptions(select, selected, emptyLabel) {
    select.replaceChildren(new Option(emptyLabel, ''), ...catalog.stages.map(row => new Option(row.name, String(row.id))));
    if(select===stageFilter)MF.set(select,selected);else select.value=selected||'';
  }
  function groupLabel(row) {
    const stage = catalog.stages.find(stage => stage.id === row.stage_id);
    return (stage ? stage.name + ' → ' : '') + row.name;
  }
  function status(id, text, error = false) {
    $(id).textContent = text;
    $(id).classList.toggle('error-text', error);
  }
  async function api(url, options = {}) {
    const response = await fetch(url, {...options, cache: 'no-store', headers: {
      'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf
    }});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось загрузить справочники.');
    return data;
  }
  function options(select, selected, emptyLabel) {
    const empty = new Option(emptyLabel, '');
    select.replaceChildren(empty, ...catalog.objects.filter(row => MF.matches(MF.get(stageFilter),row.stage_id)).map(row => new Option(groupLabel(row), String(row.id))));
    if(select===filter)MF.set(select,selected);else select.value=catalog.objects.some(row => String(row.id)===String(selected))?String(selected):'';
  }
  function render() {
    const query = search.value.trim().toLocaleLowerCase('ru');
    const objectId = MF.get(filter);
    const stageId = MF.get(stageFilter);
    const inStage = row => MF.matches(stageId,row.stage_id);
    const stageGroups = new Set(catalog.objects.filter(inStage).map(row => row.id));
    stageSummary.replaceChildren(...catalog.stages.map(stage => {
      const count = catalog.objects.filter(row => row.stage_id === stage.id).length;
      const button = element('button', stage.name + ' · групп: ' + count, 'secondary-button');
      button.type = 'button'; button.setAttribute('aria-pressed', String(MF.values(stageId).includes(String(stage.id))));
      button.addEventListener('click', () => { MF.set(stageFilter, String(stage.id)); MF.set(filter, ''); options(filter, '', 'Все группы'); render(); });
      return button;
    }));
    const names = new Map(catalog.objects.map(row => [row.id, groupLabel(row)]));
    const counts = new Map();
    catalog.subobjects.forEach(row => counts.set(row.object_id, (counts.get(row.object_id) || 0) + 1));
    const matches = text => text.toLocaleLowerCase('ru').includes(query);
    const stageCounts = new Map();
    catalog.objects.forEach(row => stageCounts.set(row.stage_id, (stageCounts.get(row.stage_id) || 0) + 1));
    const stages = catalog.stages.filter(row => matches(row.name));
    const matchedParents = new Set(catalog.subobjects.filter(row => matches(row.name)).map(row => row.object_id));
    const groups = catalog.objects.filter(row => inStage(row) && MF.matches(objectId,row.id) && (matches(groupLabel(row)) || matchedParents.has(row.id)));
    const sites = catalog.subobjects.filter(row => stageGroups.has(row.object_id) && MF.matches(objectId,row.object_id) && matches((names.get(row.object_id) || '') + ' ' + row.name));
    function list(id, rows, kind) {
      $(id).replaceChildren(...rows.map(row => {
        const text = element('div');
        const description = kind === 'stages'
          ? 'Групп: ' + (stageCounts.get(row.id) || 0)
          : kind === 'objects' ? (catalog.stages.find(stage => stage.id === row.stage_id)?.name || 'Без этапа') + ' · Подобъектов: ' + (counts.get(row.id) || 0) : names.get(row.object_id);
        text.append(element('strong', row.name), element('small', description));
        if (kind === 'stages' && stageCounts.get(row.id)) {
          text.append(element('small', 'Для удаления сначала перенесите группы в другой этап.'));
        }
        const button = element('button', 'Изменить', 'secondary-button');
        button.type = 'button'; button.setAttribute('aria-label', 'Изменить ' + row.name);
        button.addEventListener('click', () => open(kind, row));
        const card = element('div', null, 'location-catalog-row');
        card.append(text);
        if (canEdit) {
          const actions = element('div', null, 'category-actions');
          const removeButton = element('button', 'Удалить', 'text-button error-text');
          removeButton.type = 'button'; removeButton.setAttribute('aria-label', 'Удалить ' + row.name);
          removeButton.disabled = kind === 'stages' && Boolean(stageCounts.get(row.id));
          removeButton.addEventListener('click', () => remove(kind, row));
          actions.append(button, removeButton); card.append(actions);
        }
        return card;
      }));
      if (!rows.length) $(id).append(element('p', 'Ничего не найдено.', 'empty-state'));
    }
    list('location-stage-list', stages, 'stages');
    list('location-group-list', groups, 'objects');
    list('location-site-list', sites, 'subobjects');
    $('location-site-add').disabled = !catalog.objects.length;
    status('location-catalog-status', section === 'stages' ? `Этапов: ${stages.length} из ${catalog.stages.length}` : `Групп: ${groups.length} из ${catalog.objects.length} · Подобъектов: ${sites.length} из ${catalog.subobjects.length}`);
  }
  async function load() {
    if (editing || busy || loading) return;
    loading = true; panel.inert = true;
    status('location-catalog-status', 'Загрузка справочников…');
    try {
      catalog = await api('/api/locations');
      const tokens = new Map(catalog.stage_details.map(row => [row.id, row.edit_token]));
      catalog.stages.forEach(row => { row.edit_token = tokens.get(row.id); });
      stageOptions(stageFilter, MF.get(stageFilter), 'Все этапы');
      options(filter, MF.get(filter), 'Все группы');
      render();
    } catch (error) { status('location-catalog-status', error.message, true); }
    finally { loading = false; panel.inert = false; }
  }
  async function remove(kind, row) {
    if (!canEdit || editing || busy || loading) return;
    if (!confirm('Удалить ' + ({stages: 'этап', objects: 'группу', subobjects: 'подобъект'}[kind]) + ' «' + row.name + '» из справочника? Это действие нельзя отменить.')) return;
    busy = true; panel.inert = true;
    status('location-catalog-status', 'Удаление…');
    try {
      await api('/api/locations/' + kind + '/' + row.id, {method: 'DELETE', body: JSON.stringify({expected_token: row.edit_token})});
      catalog[kind] = catalog[kind].filter(item => item.id !== row.id);
      window.appReference.invalidate();
      stageOptions(stageFilter, MF.get(stageFilter), 'Все этапы');
      options(filter, MF.get(filter), 'Все группы'); render();
      status('location-catalog-status', 'Запись удалена.');
    } catch (error) { status('location-catalog-status', error.message, true); }
    finally { busy = false; panel.inert = false; }
  }
  function open(kind, row = null) {
    if (!canEdit || busy || loading) return;
    editing = {kind, row, group: String(row?.object_id || (MF.values(MF.get(filter)).length===1?MF.values(MF.get(filter))[0]:'') || ''), stage: String(row?.stage_id || (MF.values(MF.get(stageFilter)).length===1?MF.values(MF.get(stageFilter))[0]:'') || '')};
    stageEditorLabel.hidden = kind !== 'objects';
    stageOptions(stageEditor, editing.stage, 'Без этапа');
    $('location-editor-title').textContent = (row ? 'Изменить ' : 'Добавить ') + {stages: 'этап', objects: 'группу', subobjects: 'подобъект'}[kind];
    $('location-editor-name').value = row?.name || '';
    $('location-editor-group-label').hidden = kind !== 'subobjects';
    $('location-editor-group').required = kind === 'subobjects';
    options($('location-editor-group'), editing.group, 'Выберите группу');
    $('location-editor-note').textContent = kind === 'stages'
      ? (row ? 'Переименование применяется ко всем связанным группам за все даты. Привязки и расстановка сохранятся.' : 'После сохранения этап можно выбрать при редактировании группы подобъектов.')
      : row ? (kind === 'objects'
      ? 'Новое название отобразится у всех подобъектов этой группы, в том числе в прошлых периодах.'
      : 'Назначения и планы сохранятся. При смене группы подобъект перейдёт в неё во всех периодах, включая прошлые.') : 'После сохранения запись станет доступна в расстановке, плане и сводке.';
    status('location-editor-status', '');
    dialog.showModal(); $('location-editor-name').focus();
  }
  function dirty() {
    return editing && ($('location-editor-name').value !== (editing.row?.name || '') ||
      (editing.kind === 'subobjects' && $('location-editor-group').value !== editing.group) ||
      (editing.kind === 'objects' && stageEditor.value !== editing.stage));
  }
  function cancel() {
    if (busy || (dirty() && !confirm('Отменить несохранённые изменения справочника?'))) return;
    dialog.close(); editing = null;
  }
  dialog?.addEventListener('cancel', event => { event.preventDefault(); cancel(); });
  $('location-editor-cancel')?.addEventListener('click', cancel);
  form?.addEventListener('submit', async event => {
    event.preventDefault();
    if (!canEdit || busy || !editing) return;
    busy = true;
    const {kind, row} = editing;
    const data = {name: $('location-editor-name').value};
    if (kind === 'objects') data.stage_id = stageEditor.value ? Number(stageEditor.value) : null;
    if (kind === 'subobjects') data.object_id = Number($('location-editor-group').value);
    if (row) data.expected_token = row.edit_token;
    [...form.querySelectorAll('input, select, button')].forEach(node => { node.disabled = true; });
    status('location-editor-status', 'Сохранение…');
    let saved = false;
    try {
      await api('/api/locations/' + kind + (row ? '/' + row.id : ''), {method: row ? 'PATCH' : 'POST', body: JSON.stringify(data)});
      window.appReference.invalidate();
      dialog.close(); editing = null; saved = true;
    } catch (error) { status('location-editor-status', error.message, true); }
    finally {
      busy = false;
      [...form.querySelectorAll('input, select, button')].forEach(node => { node.disabled = false; });
    }
    if (saved) {
      search.value = ''; if (kind === 'subobjects') MF.set(filter, String(data.object_id));
      await load();
    }
  });
  stageFilter.addEventListener('change', () => { MF.set(filter, ''); options(filter, '', 'Все группы'); render(); });
  search.addEventListener('input', render);
  filter.addEventListener('change', render);
  $('location-catalog-refresh').addEventListener('click', () => { window.appReference.invalidate(); load(); });
  $('location-group-add').addEventListener('click', () => open('objects'));
  $('location-stage-add').addEventListener('click', () => open('stages'));
  $('location-site-add').addEventListener('click', () => open('subobjects'));
  window.addEventListener('beforeunload', event => {
    if (busy || dirty()) { event.preventDefault(); event.returnValue = ''; }
  });
  function setSection(next) {
    section = next;
    stageLabel.hidden = stageSummary.hidden = next === 'stages';
    filter.closest('label').hidden = next === 'stages';
    search.placeholder = next === 'stages' ? 'Название этапа' : 'Название группы или подобъекта';
    search.value = '';
  }
  window.locationsScreen = {load, setSection, canLeave: () => !busy && !editing && !loading};
})();
