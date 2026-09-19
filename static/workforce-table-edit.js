(() => {
  'use strict';
  window.createWorkforceTableEdit = ({api, E, board, reload, announce, canEdit = () => true}) => {
    const table = document.querySelector('#wf-table-region table');
    const host = document.querySelector('#wf-inline-edit');
    const notice = document.querySelector('#wf-selection-notice');
    const controls = new Map();
    const lazyOptions = new WeakMap();
    function populateOptions(select) {
      const next=lazyOptions.get(select);
      if (!next || next.rendered) return;
      select.replaceChildren(E('option',{value:''},next.placeholder),...next.values.map(item=>E('option',{value:item.value},item.label)));
      next.rendered=true;
    }
    let activeRow = null;
    function closeRow() {
      if (saving) return;
      const editor = activeRow; activeRow = null; editor?.close();
    }
    let signature = '', generation = 0, timer, prepared, rows = [], query, reference, saving = false;
    const autoCategory = E('button', {type:'button',className:'secondary-button',hidden:true,disabled:true,
      title:'Подобрать категорию ГДЛР выбранным сотрудникам'}, 'Авто ГДЛР');
    autoCategory.id = 'wf-auto-category';
    notice.before(autoCategory);
    autoCategory.addEventListener('click', previewCategories);
    const editRow = E('tr', {className: 'wf-column-edit-row', 'aria-label': 'Изменить выбранным сотрудникам'});
    [...table.tHead.rows[0].cells].forEach((header, index) => {
      const field = header.dataset.editField, title = header.textContent.trim();
      const cell = E('td', {className: field ? 'wf-column-edit-cell' : 'wf-edit-empty'});
      if (header.dataset.column) cell.dataset.column = header.dataset.column;
      if (field) {
        const select = header.dataset.editType === 'date' ? E('input', {type:'date',disabled:true,'aria-label':'Изменить выбранным: ' + title}) :
          E('select', {disabled: true, 'aria-label': 'Изменить выбранным: ' + title}, E('option', {value:''}, 'Изменить…'));
        select.addEventListener('change', () => choose(field, select.value));
        if (select.tagName === 'SELECT') {
          select.addEventListener('pointerdown',()=>populateOptions(select));
          select.addEventListener('focus',()=>populateOptions(select));
          select.addEventListener('keydown',()=>populateOptions(select));
        }
        controls.set(field, select);
        cell.append(E('label', {}, E('span', {className:'wf-column-edit-label'}, title), select));
      } else if (header.dataset.column === 'name') cell.textContent = 'Выбранным →';
      editRow.append(cell);
    });
    table.tHead.append(editRow);
    new ResizeObserver(() => table.style.setProperty('--wf-header-height', table.tHead.rows[0].getBoundingClientRect().height + 'px')).observe(table.tHead.rows[0]);
    function clear() {
      closeRow();
      board.closeInline();
      if (host.childNodes.length) host.replaceChildren();host.hidden = true;
      for (const select of controls.values()) select.value = '';
    }
    function options(field, values, enabled, placeholder = 'Изменить…') {
      const select = controls.get(field);
      if (select.tagName === 'SELECT') {
        const signature=JSON.stringify([placeholder,values]);
        const previous=lazyOptions.get(select);
        if (previous?.signature !== signature) {
          lazyOptions.set(select,{values,placeholder,signature,rendered:false});
          if (select.options.length>1) select.replaceChildren(E('option',{value:''},placeholder));
          else if (select.options[0].textContent!==placeholder) select.options[0].textContent=placeholder;
        }
        if (values.length<=30 || document.activeElement===select) populateOptions(select);
      }
      if (select.disabled === enabled) select.disabled = !enabled;
      select.title = !rows.length ? 'Сначала отметьте сотрудников чекбоксами' : !enabled ? placeholder : select.getAttribute('aria-label');
    }
    function sync(selected, listQuery, catalog, visible) {
      autoCategory.hidden = !visible || !catalog?.permissions?.profile;
      autoCategory.disabled = !selected.length || saving;
      editRow.hidden = !visible || !(catalog?.permissions?.profile || catalog?.permissions?.transition);
      const next = JSON.stringify([visible, selected.map(row => row.id), listQuery?.toString(), catalog?.permissions]);
      if (next === signature) return;
      signature = next;const request = ++generation;clearTimeout(timer);clear();prepared = null;
      rows = [...selected];query = listQuery;reference = catalog;notice.textContent = '';
      for (const field of controls.keys()) options(field, [], false);
      if (!visible || !rows.length) return;
      const allowed = (catalog?.catalog || []).filter(item => item.kind === 'stage' && item.active &&
        catalog.permissions.transition && rows.every(row => row.stage_token && row.transition_targets?.includes(item.code)));
      options('stage_code', allowed.map(item => ({value:item.code,label:item.label})), !!allowed.length,
        allowed.length ? 'Изменить…' : 'Нет общего перехода');
      if (!catalog?.permissions?.profile) return;
      for (const field of controls.keys()) if (field !== 'stage_code') options(field, [], false, 'Загрузка…');
      timer = setTimeout(async () => {
        try {
          const data = await api('bulk/table/prepare', {method:'POST',body:JSON.stringify({ids:rows.map(row=>row.id),date:query.get('date')})});
          if (request !== generation) return;
          prepared = data;
          for (const [field, catalog] of Object.entries(data.fields)) if (controls.has(field)) {
            options(field, catalog.options, catalog.enabled, catalog.enabled ? 'Изменить…' : 'Недоступно');
            controls.get(field).title = catalog.note || controls.get(field).getAttribute('aria-label');
          }
        } catch (error) {
          if (request !== generation) return;
          for (const field of controls.keys()) if (field !== 'stage_code') options(field, [], false, 'Недоступно');
          notice.textContent = error.message;
        }
      }, 100);
    }
    async function previewCategories() {
      if (saving || board.busy() || !rows.length || !reference?.permissions?.profile) return;
      clear();
      const revision = generation, ids = rows.map(row => row.id);
      saving = true;autoCategory.disabled = true;notice.textContent = 'Подбор ГДЛР…';
      try {
        const data = await api('bulk/category-auto/prepare', {method:'POST',body:JSON.stringify({ids})});
        if (revision !== generation) return;
        notice.textContent = '';host.hidden = false;
        const preview = E('div', {className:'wf-bulk-preview'});
        const titles = ['ФИО', 'Должность', 'ГДЛР сейчас', 'Результат подбора'];
        const body = E('tbody');
        for (const row of data.people) {
          const result = row.status === 'matched' ? row.category + ' · ' + row.basis : row.basis;
          body.append(E('tr', {}, ...[row.name,row.profession || '—',row.current || '—',result].map(
            (value,index)=>E('td', {'data-label':titles[index]}, value))));
        }
        preview.append(E('table', {}, E('thead', {}, E('tr', {}, ...titles.map(title=>E('th', {}, title)))), body));
        const reason = E('input', {type:'text',maxLength:10000,placeholder:'Причина назначения ГДЛР (необязательно)',
          'aria-label':'Причина назначения ГДЛР (необязательно)'});
        const apply = E('button', {type:'submit',className:'primary-button',disabled:!data.matched}, `Применить (${data.matched})`);
        const cancel = E('button', {type:'button',className:'secondary-button',onclick:clear}, 'Закрыть');
        const message = E('span', {role:'status',className:'error-text'});
        const form = E('form', {className:'wf-bulk-form'},
          E('strong', {}, `Автоподбор ГДЛР: к назначению ${data.matched} из ${data.people.length}`),
          E('small', {}, 'По назначенным ГДЛР сотрудников с той же должностью в справочнике. Только единственный вариант; заполненные категории сохраняются.'),
          preview, reason, E('div', {className:'wf-bulk-actions'}, apply, cancel), message);
        let attempt;
        form.addEventListener('submit', async event => {
          event.preventDefault();if (saving || !data.matched || !form.reportValidity()) return;
          const payload = {ids,token:data.token,reason:reason.value.trim()};
          const fingerprint = JSON.stringify(payload);
          if (attempt?.fingerprint !== fingerprint) attempt = {fingerprint,key:crypto.randomUUID()};
          saving = true;document.querySelector('#wf-list-workspace').inert = true;message.textContent = 'Сохранение…';
          try {
            const result = await api('bulk/category-auto/apply', {method:'POST',body:JSON.stringify({...payload,request_key:attempt.key})});
            saving = false;clear();await reload();
            announce(`ГДЛР назначена: ${result.changed}. Без изменения: ${result.skipped}.`);
          } catch (error) {message.textContent = error.message;}
          finally {saving = false;document.querySelector('#wf-list-workspace').inert = false;}
        });
        host.replaceChildren(form);
      } catch (error) {if (revision === generation) notice.textContent = error.message;}
      finally {saving = false;autoCategory.disabled = !rows.length;}
    }
    function choose(field, value, context = null) {
      if (saving || board.busy()) return;
      const target = context?.host || host;
      const finish = context ? () => {closeRow();} : clear;
      if (!context) {clear();if (!value) return;controls.get(field).value = value;}
      if (field === 'stage_code') {
        board.transitionRows(context?.rows || rows, context?.query || query, context?.reference || reference,
          notice, value, null, context ? () => {context.close();} : () => {controls.get(field).value = '';});
        return;
      }
      target.hidden = false;
      const data = context?.data || prepared;
      if (!data) return;
      const catalog = data.fields[field];
      if (!catalog.enabled) return;
      const option = catalog.type === 'date' ? {label:value.split('-').reverse().join('.')} : catalog.options.find(item => item.value === value);
      if (!context && !option) return;
      if (field === 'project_code') {
        const project = context?.input || E('select', {'aria-label':'Проект'}, ...catalog.options.map(o=>E('option',{value:o.value},o.label)));
        project.value = value;
        const select = E('select', {'aria-label':'СМУ выбранного проекта'});
        const update = () => {
          const ids = catalog.smus[project.value] || [];
          select.replaceChildren(E('option',{value:''},'Выберите СМУ'), ...data.fields.smu_id.options.filter(s=>ids.includes(s.value)).map(s=>E('option',{value:s.value},s.label)));
        };
        project.addEventListener('change',update);update();
        select.addEventListener('change',()=> {
          if (!select.value) return;
          if (context) choose('smu_id',select.value,{...context,input:select});
          else choose('smu_id',select.value);
        });
        const cancel = E('button',{type:'button',className:'secondary-button',onclick:finish},'Отмена');
        target.replaceChildren(E('div',{className:'wf-cell-project'},project,
          E('small',{},'Проект определяется выбранным СМУ.'),select,cancel));return;
      }
      const reason = E('input', {type:'text',maxLength:10000,placeholder:'Причина изменения для истории (необязательно)', 'aria-label':'Причина изменения (необязательно)'});
      const apply = E('button', {type:'submit',className:'primary-button'}, context ? '✓' : `Применить выбранным (${data.people.length})`);
      const cancel = E('button', {type:'button',className:'secondary-button',onclick:finish}, 'Отмена');
      const message = E('span', {role:'status',className:'error-text'});
      const form = E('form', {className:context ? 'wf-cell-form' : 'wf-inline-reference'},
        ...(context ? [context.input] : [E('strong', {}, catalog.label + ' → ' + option.label)]), reason,
        E('div',{className:'wf-cell-actions'},apply,cancel), message);
      if (context) {
        reason.hidden = true;
        apply.setAttribute('aria-label', 'Сохранить: ' + catalog.label);
        cancel.setAttribute('aria-label', 'Отменить: ' + catalog.label);
      }
      const extras = new Map();
      const add = (key,label,input) => {extras.set(key,input);form.insertBefore(E('label',{},label,input),reason);};
      const select = (values,label) => E('select',{required:true,'aria-label':label},E('option',{value:''},'Выберите…'),...values.map(o=>E('option',{value:o.value},o.label)));
      if (catalog.note) {
        if (context) context.input.title = catalog.note;
        else form.insertBefore(E('small',{},catalog.note),reason);
      }
      if (catalog.create_movement) {
        form.insertBefore(E('small',{},`У ${catalog.create_movement} сотрудников нет незавершённой поездки. Для них будет создан новый план:`),reason);
        if (field !== 'movement_direction') add('direction','Направление новой поездки',select(data.fields.movement_direction.options,'Направление новой поездки'));
        if (field !== 'planned_date') add('planned_date','Дата новой поездки',E('input',{type:'date',required:true,'aria-label':'Дата новой поездки'}));
        add('destination_kind','Пункт назначения новой поездки',select(data.fields.movement_destination.options,'Пункт назначения новой поездки'));
      }
      if (catalog.create_rotation) {
        form.insertBefore(E('small',{},`У ${catalog.create_rotation} сотрудников нет открытой вахты. Для них будет создана вахта с указанным началом и графиком:`),reason);
        add('start_date','Начало новой вахты',E('input',{type:'date',required:true,'aria-label':'Начало новой вахты'}));
        if (field !== 'rotation_schedule_id') add('schedule_id','График новой вахты',select(data.fields.rotation_schedule_id.options,'График новой вахты'));
      }
      if (['rotation_schedule_id','forecast_departure_date','leave_end_date'].includes(field)) {
        add('recalculate','Пересчитать следующие даты по графику',E('input',{type:'checkbox',disabled:catalog.recalculate_enabled === false,
          title:catalog.recalculate_enabled === false ? catalog.note : '', 'aria-label':'Пересчитать следующие даты по графику'}));
      }
      let attempt;
      form.addEventListener('submit', async event => {
        event.preventDefault();if (saving || !form.reportValidity()) return;
        const extra = Object.fromEntries([...extras].map(([key,input])=>[key,input.type === 'checkbox' ? input.checked : input.value]));
        const payload = {field,value:context ? context.input.value : value,date:data.date,extra,reference_token:catalog.token,people:data.people.map(({id,token})=>({id,token})),reason:reason.value.trim()};
        const fingerprint = JSON.stringify(payload);
        if (attempt?.fingerprint !== fingerprint) attempt = {fingerprint,key:crypto.randomUUID()};
        saving = true;document.querySelector('#wf-list-workspace').inert = true;message.textContent = 'Сохранение…';
        try {
          const result = await api('bulk/table/apply', {method:'POST',body:JSON.stringify({...payload,request_key:attempt.key})});
          saving = false;finish();await reload();
          announce(`Обновлено: ${result.changed}. Уже имели это значение: ${result.unchanged}.`);
        } catch (error) {message.textContent = error.message;}
        finally {saving = false;document.querySelector('#wf-list-workspace').inert = false;}
      });
      target.replaceChildren(form);
      if (context) context.input.focus({preventScroll:true});
    }
    function openCellInput(context) {
      const input = context.input;
      input.focus({preventScroll:true});
      if (input.type === 'date') {
        if (!context.calendar) {
          context.calendar = window.DateFilter.mount({title:input.getAttribute('aria-label'),single:true,allowAll:false,allowEmpty:false,
            onChange:value => {input.value = value.from;},
            onClose:() => {if (activeRow === context && input.isConnected) input.focus({preventScroll:true});}});
          input.addEventListener('click',event => {event.preventDefault();openCellInput(context);});
        }
        context.calendar.set({from:input.value,to:input.value});
        context.calendar.open();
      } else if (input.showPicker) {
        // The native control remains usable when browser activation has expired during a slow request.
        try {input.showPicker();} catch (_) { /* Keyboard and pointer selection remain available. */ }
      }
    }
    function decorate(cell, row, header, listQuery, catalog) {
      const field = header.dataset.editField;
      if (!field || !row.active || !(field === 'stage_code' ? catalog?.permissions?.transition : catalog?.permissions?.profile)) return;
      const values = field === 'stage_code' ? (catalog.catalog || []).filter(item => item.kind === 'stage' && item.active && row.stage_token && row.transition_targets?.includes(item.code)).map(item => ({value:item.code,label:item.label})) : null;
      if (values && !values.length) return;
      const original = cell.firstChild;
      let editorHost;
      const trigger = E('button',{type:'button',className:'wf-cell-trigger','data-edit-field':field,
        title:'Изменить: ' + header.textContent.trim(), 'aria-label':'Изменить ' + header.textContent.trim() + ': ' + row.full_name},
        original);
      cell.prepend(trigger);
      trigger.addEventListener('click', async () => {
        if (saving || board.busy() || !canEdit()) return;
        clear();
        editorHost = E('div',{className:'wf-cell-editor'});
        trigger.replaceWith(editorHost);
        const context = {host:editorHost,rows:[row],query:new URLSearchParams(listQuery),reference:catalog,
          close:() => {context.calendar?.close();editorHost.replaceWith(trigger);board.closeInline();if (activeRow === context) activeRow = null;}};
        activeRow = context;
        const cancel = () => E('button',{type:'button',className:'secondary-button',onclick:()=>{closeRow();trigger.focus();},
          'aria-label':'Отменить: ' + header.textContent.trim()},'Отмена');
        const message = E('small',{role:'status'},'Загрузка…');
        editorHost.replaceChildren(message,cancel());
        try {
          if (values) {
            const select = E('select',{'aria-label':header.textContent.trim()+': '+row.full_name},E('option',{value:''},'Выберите…'),...values.map(o=>E('option',{value:o.value},o.label)));
            select.addEventListener('change',()=>{if (select.value) choose(field,select.value,context);});
            editorHost.replaceChildren(select,cancel());context.input = select;openCellInput(context);return;
          }
          context.data = await api('bulk/table/prepare',{method:'POST',body:JSON.stringify({ids:[row.id],date:context.query.get('date'),field})});
          if (activeRow !== context || !editorHost.isConnected) return;
          const definition = context.data.fields[field], current = context.data.people[0].values[field];
          if (!definition.enabled) {message.textContent = definition.note || 'Редактирование недоступно';return;}
          context.input = definition.type === 'date' ? E('input',{type:'date',value:current,required:true}) : E('select',{required:true},
            E('option',{value:'',disabled:true},current ? 'Текущее значение недоступно' : 'Не указано'),...definition.options.map(o=>E('option',{value:o.value},o.label)));
          context.input.setAttribute('aria-label',header.textContent.trim()+': '+row.full_name);
          context.input.value = current;
          if (context.input.value === null) context.input.value = '';
          choose(field,current,context);
          openCellInput(context);
        } catch (error) {if (activeRow === context) message.textContent = error.message;}
      });
    }
    return {sync,decorate,closeRow,editing:cell=>!!activeRow && cell.contains(activeRow.host),busy:()=>saving};
  };
})();
