(() => {
  'use strict';
  const $ = id => document.getElementById(id), root = document.querySelector('.app-shell');
  if (!$('view-crew-catalog')) return;
  const readOnly = ['hr_viewer', 'rotation', 'recruitment'].includes(root.dataset.role);
  let rows = [], busy = false, editing = false;
  const el = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key,value] of Object.entries(props)) {
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key in node) node[key] = value;
      else node.setAttribute(key,value);
    }
    node.append(...children.flat()); return node;
  };
  function status(text, error = false) {
    $('crew-catalog-status').textContent = text;
    $('crew-catalog-status').classList.toggle('error-text',error);
  }
  async function api(url, options = {}) {
    if (options.method && options.method !== 'GET') window.catalogData.invalidate();
    const response = await fetch(url, {...options, cache:'no-store', headers:{
      'Content-Type':'application/json', 'X-CSRF-Token':root.dataset.csrf}}), data = await response.json();
    if (!response.ok) throw Error(data.error || 'Не удалось загрузить бригады.');
    return data;
  }
  function canLeave() {
    if (busy || editing) { status('Завершите работу с открытой формой.',true); return false; }
    return true;
  }
  async function load(refresh = false) {
    if (!canLeave()) return;
    busy = true; $('view-crew-catalog').inert = true; status('Загрузка бригад…');
    try { rows = (await window.catalogData.get('/api/crew-catalog', () => api('/api/crew-catalog'), {refresh})).rows; status(''); render(); }
    catch(error) { status(error.message,true); }
    finally { busy = false; $('view-crew-catalog').inert = false; }
  }
  function edit(crew = null) {
    if (readOnly) return;
    if (!canLeave()) return;
    editing = true;
    window.crewCreator.open({crew, rows:[], snapshot:{},
      onClosed: () => { editing = false; $('crew-catalog-add').focus(); },
      onSaved: async () => { window.catalogData.invalidate();await load(); if (!$('crew-catalog-status').classList.contains('error-text')) status(crew ? 'Бригада сохранена.' : 'Бригада создана.'); }});
  }
  function remove(crew, trigger) {
    if (!canLeave()) return;
    editing = true;
    let saving = false;
    const close = () => {
      if (saving) return;
      dialog.close(); dialog.remove(); editing = false;
      (trigger.isConnected ? trigger : $('crew-catalog-search')).focus();
    };
    const message = el('p',{className:'save-status',role:'status'});
    const cancel = el('button',{type:'button',className:'secondary-button',onclick:close},crew.can_delete ? 'Отмена' : 'Закрыть');
    const confirm = el('button',{type:'button',className:'primary-button crew-catalog-delete',onclick:async () => {
      if (saving || !crew.can_delete) return;
      saving = true; confirm.disabled = true; cancel.disabled = true;
      message.classList.remove('error-text'); message.textContent = 'Удаление бригады…';
      let deleted = false;
      try {
        await api('/api/crew-catalog/' + crew.id,{method:'DELETE',body:JSON.stringify({expected_token:crew.expected_token})});
        deleted = true;
      } catch(error) { message.textContent = error.message; message.classList.add('error-text'); }
      finally { saving = false; confirm.disabled = false; cancel.disabled = false; }
      if (deleted) {
        close(); rows = rows.filter(row => row.id !== crew.id); render();
        $('crew-catalog-search').focus();
        status('Бригада «' + crew.name + '» удалена.');
      }
    }},'Удалить бригаду');
    const dialog = el('dialog',{className:'app-dialog crew-catalog-dialog','aria-labelledby':'crew-delete-title'},
      el('h2',{id:'crew-delete-title'},crew.can_delete ? 'Удалить бригаду?' : 'Удаление недоступно'),
      el('p',{},el('strong',{},crew.name)),
      el('p',{},crew.can_delete ? 'Бригада пустая и не используется в расстановке или истории. Перед удалением будет создана резервная копия.' : crew.delete_reason),
      ...(crew.can_delete && crew.imported ? [el('p',{},'Если бригада остаётся в исходном файле, следующий импорт может создать её снова.')] : []),
      message,el('div',{className:'crew-catalog-actions'},cancel,...(crew.can_delete ? [confirm] : [])));
    dialog.addEventListener('cancel',event => { event.preventDefault(); close(); });
    document.body.append(dialog); dialog.showModal(); cancel.focus();
  }
  async function members(crew) {
    if (!canLeave()) return;
    editing = true;
    const list = el('div', {className:'crew-catalog-members'}, 'Загрузка состава…');
    const close = () => { dialog.close(); dialog.remove(); editing = false; $('crew-catalog-search').focus(); };
    const dialog = el('dialog', {className:'app-dialog crew-catalog-dialog', 'aria-labelledby':'crew-catalog-dialog-title'},
      el('h2',{id:'crew-catalog-dialog-title'}, 'Состав: ' + crew.name), list,
      el('button',{type:'button',className:'secondary-button',onclick:close},'Закрыть'));
    dialog.addEventListener('cancel',event => { event.preventDefault(); close(); });
    document.body.append(dialog); dialog.showModal();
    try {
      const data = await api('/api/crew-catalog/' + crew.id + '/members');
      if (!dialog.isConnected) return;
      list.replaceChildren(...(data.rows.length ? data.rows.map(row => el('article',{},
        el('strong',{},row.full_name),
        el('p',{},'Таб. № ' + (row.personnel_no || '—') + ' · ' + (row.category || 'Категория не указана')),
        el('p',{},(row.department || 'СМУ не указано') + ' · ' + (row.employer || 'Работодатель не указан')),
        ...(!row.active ? [el('small',{},'Неактивен')] : []))) : [el('p',{},'В бригаде пока нет сотрудников.')]));
    } catch(error) { if (dialog.isConnected) list.textContent = error.message; }
  }
  function render() {
    const query = $('crew-catalog-search').value.trim(); let expression;
    try { expression = $('crew-catalog-regex').checked && query ? new RegExp(SearchRegex.source(query),'iu') : null; }
    catch (_) { status('Некорректный Regex. Исправьте выражение.',true); return; }
    status('');
    const visible = rows.filter(row => {
      const text = [row.name,row.linear_itr,row.brigadier,...row.departments].join(' ');
      return expression ? expression.test(text) : text.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru'));
    });
    $('crew-catalog-count').textContent = 'Бригад: ' + visible.length + ' из ' + rows.length + '. Сотрудников в показанных бригадах: ' + visible.reduce((n,row)=>n+row.member_count,0) + '.';
    const head = el('thead',{},el('tr',{},...['Бригада','Линейный ИТР','Бригадир','СМУ','Состав',...(!readOnly ? ['Действия'] : [])].map(text=>el('th',{scope:'col'},text))));
    const cell = (label,child) => el('td',{'data-label':label},child);
    const body = el('tbody',{},...visible.map(row => el('tr',{},
      cell('Бригада',el('strong',{},row.name)), cell('Линейный ИТР',row.linear_itr || '—'),cell('Бригадир',row.brigadier || '—'),
      cell('СМУ',row.departments.join(', ') || '—'),
      cell('Состав',el('div',{},el('button',{type:'button',className:'text-button',onclick:()=>members(row), 'aria-label':'Состав бригады: ' + row.name},row.member_count + ' чел.'),
        ...(row.active_count < row.member_count ? [el('small',{},'Активных: ' + row.active_count)] : []))),
      ...(!readOnly ? [cell('Действия',el('div',{className:'crew-catalog-actions'},el('button',{type:'button',className:'secondary-button',disabled:!row.can_edit,
        title:row.can_edit ? 'Редактировать бригаду' : 'Для изменения нужен доступ ко всем СМУ этой бригады.',
        'aria-label':'Редактировать бригаду: ' + row.name,onclick:()=>edit(row)},'Редактировать'),
        ...(root.dataset.role === 'super_admin' ? [el('button',{type:'button',className:'text-button crew-catalog-delete',
          'aria-label':'Удалить бригаду: ' + row.name,onclick:event=>remove(row,event.currentTarget)},'Удалить')] : [])))] : []))));
    $('crew-catalog-list').replaceChildren(visible.length ? el('table',{className:'crew-catalog-table'},head,body) : el('p',{},'Бригады не найдены.'));
  }
  $('crew-catalog-add').addEventListener('click',()=>edit());
  $('crew-catalog-refresh').addEventListener('click',()=>load(true));
  $('crew-catalog-search').addEventListener('input',render);
  $('crew-catalog-regex').addEventListener('change',render);
  window.crewCatalogScreen = {load,canLeave};
})();
