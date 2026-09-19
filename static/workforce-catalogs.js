(() => {
  'use strict';
  const root=document.querySelector('.app-shell');
  const pageSize=50;
  let panel, reference, kind='organization', search='', offset=0, busy=false, dirty=false, loading=false, loadId=0, discardedDirty=false;
  const kinds=[['organization','Работодатели'],['citizenship','Гражданство'],['profession','Должности и профессии'],['travelpoint','Пункты поездок'],['place','Места ПВП'],['schedule','Графики вахтования'],
    ['document','Документы'],['check','Проверки оформления'],['project','Проекты'],['employment','Статус сотрудника'],['accommodation','Проживание'],
    ['stage','Присутствие'],['direction','Направление поездки'],['destination','Тип места назначения'],['basis','Основание поездки'],['result','Результат поездки'],['docstate','Состояние документа'],['checkstate','Состояние проверки']];
  const fixed=new Set(['employment','stage','direction','destination','basis','result','docstate','checkstate']);
  const openSpecialties=new Set();
  const isActive=row=>row.active!==false&&row.active!==0;
  const normalize=value=>String(value??'').toLocaleLowerCase('ru').replace(/ё/g,'е').trim();
  const el=(tag,attributes={},...children)=>{
    const node=document.createElement(tag);
    for(const [key,value] of Object.entries(attributes)) {
      if(key.startsWith('on'))node.addEventListener(key.slice(2),value);
      else if(key in node)node[key]=value;
      else node.setAttribute(key,value);
    }
    for(const child of children.flat())if(child!=null)node.append(child);
    return node;
  };
  async function api(path,options={}) {
    if(options.method&&options.method!=='GET')window.catalogData.invalidate();
    const response=await fetch('/api/workforce/'+path,{...options,cache:'no-store',headers:{'Content-Type':'application/json','X-CSRF-Token':root.dataset.csrf}});
    return window.readApiResponse(response,'Не удалось сохранить справочник.');
  }
  const visible=()=>!!panel&&!panel.hidden&&document.getElementById('view-catalogs')?.classList.contains('active');
  const canLeave=()=>!busy&&!loading&&(!visible()||!dirty||window.confirm('В справочнике есть несохранённые изменения. Закрыть их?'));
  const discard=()=>{discardedDirty=discardedDirty||dirty;dirty=false;};
  function loadError(message) {
    panel.querySelector('.wf-catalog-load-error')?.remove();
    panel.prepend(el('p',{className:'error-text wf-catalog-load-error',role:'alert'},message));
  }
  async function load(nextKind=kind,refresh=false) {
    panel=document.getElementById('workforce-catalog-panel');if(!panel)return false;
    if(!kinds.some(([value])=>value===nextKind)){dirty=dirty||discardedDirty;discardedDirty=false;loadError('Неизвестный справочник учёта. Выберите раздел в меню.');return false;}
    const current=++loadId, previous={reference,kind,search,offset,dirty:dirty||discardedDirty};
    loading=true;panel.inert=true;
    try {
      const nextReference=await window.catalogData.get('/api/workforce/reference',()=>api('reference'),{refresh});if(current!==loadId)return false;
      reference=nextReference;
      if(nextKind!==kind){kind=nextKind;offset=0;search='';}
      render();dirty=false;discardedDirty=false;return true;
    } catch(error) {
      if(current!==loadId)return false;
      ({reference,kind,search,offset,dirty}=previous);discardedDirty=false;loadError(error.message);return false;
    } finally {if(current===loadId){loading=false;panel.inert=busy;}}
  }
  function editor(row,editorKind=kind,defaults={}) {
    const form=el('form',{className:'wf-form'}), inputs={}, initial={};let attempt;
    if(editorKind==='profession') {
      const selected=row?.specialty_code||defaults.specialty_code||'';
      const parents=reference.catalog.filter(item=>item.kind==='specialty'&&(isActive(item)||item.code===selected));
      const parent=el('select',{required:!row||!!row.specialty_code},el('option',{value:''},row&&!row.specialty_code?'Без специальности (исходное значение)':'Выберите специальность'),
        ...parents.sort((a,b)=>a.label.localeCompare(b.label,'ru')).map(item=>el('option',{value:item.code},item.label+(isActive(item)?'':' · архив'))));
      parent.value=selected;inputs.specialty_code=parent;initial.specialty_code=parent.value;
      form.append(el('label',{className:'wf-wide'},'Специальность',parent));
    }
    const fields=editorKind==='schedule' ? [['name','Название','text'],['onsite_days','Дней на участке','number'],['leave_days','Дней МО','number'],['travel_days','Дней до заезда после МО','number']] : [['label',editorKind==='profession'?'Полное название должности / профессии':'Название','text']];
    if(editorKind==='profession')fields.unshift(['grade','Разряд (если указан)','number']);
    if(editorKind==='place')fields.push(['address','Адрес','text'],['capacity','Вместимость','number']);
    for(const [key,title,type] of fields) {
      const value=key==='label' ? row?.label||row?.name||'' : row?.[key]??'';
      const input=el('input',{type,value,required:!['address','capacity','grade'].includes(key),maxLength:key==='label'&&['profession','specialty'].includes(editorKind)?500:key==='label'&&editorKind==='travelpoint'?300:200});
      if(type==='number'){input.min=key==='leave_days'||key==='capacity'?'0':'1';input.step='1';}
      if(key==='grade')input.max='99';
      inputs[key]=input;initial[key]=input.value;form.append(el('label',{className:editorKind==='profession'&&key==='label'?'wf-wide':''},title,input));
    }
    if(editorKind==='profession'&&!row) {
      let generated='';
      const suggestLabel=()=>{
        if(inputs.label.value&&inputs.label.value!==generated)return;
        const parent=reference.catalog.find(item=>item.kind==='specialty'&&item.code===inputs.specialty_code.value);
        generated=parent?parent.label+(inputs.grade.value?' '+inputs.grade.value+' разряда':''):'';
        inputs.label.value=generated;
      };
      inputs.specialty_code.addEventListener('change',suggestLabel);inputs.grade.addEventListener('input',suggestLabel);suggestLabel();
    }
    if(editorKind==='specialty'&&row)form.append(el('p',{className:'table-note wf-wide'},'Изменение названия специальности не меняет полные названия вложенных должностей и назначения работников.'));
    const initialActive=row?.active!==false&&row?.active!==0;
    const active=el('input',{type:'checkbox',checked:initialActive});
    form.append(el('label',{className:'check-label'},active,'Действующее значение'));
    const reason=el('textarea',{maxLength:10000,placeholder:'Необязательно для истории'});form.append(el('label',{className:'wf-wide'},'Основание изменения (необязательно)',reason));
    const message=el('p',{className:'error-text wf-wide',role:'alert'});
    const submit=el('button',{type:'submit',className:'primary-button wf-wide'},row?'Сохранить':'Добавить');form.append(submit,message);
    form.addEventListener('input',()=>{dirty=true;});
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(busy||!form.reportValidity())return;
      const body={reason:reason.value};
      const partial=row&&editorKind!=='schedule';
      if(!partial||active.checked!==initialActive)body.active=active.checked;
      for(const [key,input] of Object.entries(inputs))if(!partial||input.value!==initial[key])body[key]=input.type==='number'?(input.value===''?null:Number(input.value)):input.value;
      if(row)body.token=row.edit_token;
      const fingerprint=JSON.stringify(body);if(!attempt||attempt.fingerprint!==fingerprint)attempt={fingerprint,key:crypto.randomUUID()};
      body.request_key=attempt.key;busy=true;panel.inert=true;submit.disabled=true;message.textContent='';
      try {
        const id=row&&(row.id||row.code);
        const path=editorKind==='schedule'?'rotation-schedules':'catalog/'+editorKind;
        await api(path+(id?'/'+encodeURIComponent(id):''),{method:row?'PATCH':'POST',body:JSON.stringify(body)});
        dirty=false;window.workforceScreen?.invalidate();await load();
      } catch(error){message.textContent=error.message;}
      finally {busy=false;panel.inert=loading;submit.disabled=false;}
    });
    return form;
  }
  function lazyEditor(title,row,editorKind,defaults={}) {
    const section=el('details',{className:'wf-catalog-editor'},el('summary',{},title));
    let loaded=false;
    section.addEventListener('toggle',()=>{if(section.open&&!loaded){loaded=true;section.append(editor(row,editorKind,defaults));}});
    return section;
  }
  function catalogTable(columns,rows,editable,editorKind=kind) {
    const body=el('tbody');
    const table=el('table',{className:'catalog-data-table'},
      el('thead',{},el('tr',{},...columns.map(([label])=>el('th',{scope:'col'},label)),
        ...(editable?[el('th',{scope:'col',className:'catalog-action-cell'},'Действия')]:[]))),body);
    for(const row of rows) {
      const cells=columns.map(([label,value])=>el('td',{'data-label':label},value(row)));
      const line=el('tr',{},...cells);
      body.append(line);
      if(editable) {
        const actions=el('td',{'data-label':'Действия',className:'catalog-action-cell'});
        line.append(actions);
        if(!row.system_value) {
          let detail;
          const button=el('button',{type:'button',className:'secondary-button','aria-expanded':'false',
            'aria-label':'Редактировать: '+(row.label||row.name),onclick:()=>{
              if(!detail) {
                detail=el('tr',{className:'catalog-editor-row'},el('td',{colSpan:columns.length+1},editor(row,editorKind)));
                line.after(detail);
              } else detail.hidden=!detail.hidden;
              button.setAttribute('aria-expanded',String(!detail.hidden));
            }},'Редактировать');
          actions.append(button);
        } else actions.append(el('span',{className:'table-note'},'Системное'));
      }
    }
    if(!rows.length)body.append(el('tr',{},el('td',{colSpan:columns.length+(editable?1:0)},'Ничего не найдено.')));
    return table;
  }
  const statusCell=row=>el('span',{className:'catalog-status'+(isActive(row)?'':' is-inactive')},isActive(row)?'Действует':'Отключено');
  function pagination(total,unit='') {
    const info=window.TablePagination.windowFor(total,Math.floor(offset/pageSize),pageSize);
    offset=info.start;
    const container=el('div',{className:'wf-list-heading'});
    const pager=window.TablePagination.mount(container,'Справочник учёта',nextPage=>{
      if(!canLeave())return false;
      offset=nextPage*pageSize;dirty=false;discardedDirty=false;render();
    },{top:false,sizes:false,container,unit:unit.trim()});
    pager.update(total,info.page,pageSize);
    return container;
  }
  function renderProfessions(content,editable) {
    const parents=reference.catalog.filter(row=>row.kind==='specialty');
    const groups=new Map(parents.map(row=>[row.code,{key:row.code,parent:row,children:[]}]));
    const ungrouped={key:'__ungrouped__',parent:null,children:[]};
    for(const row of reference.catalog.filter(item=>item.kind==='profession'))(groups.get(row.specialty_code)||ungrouped).children.push(row);
    const query=normalize(search);
    const matches=row=>normalize(row.label).includes(query)||(row.grade!=null&&normalize(row.grade+' разряд').includes(query));
    let shown=[...groups.values(),...(ungrouped.children.length?[ungrouped]:[])].map(group=>({...group,
      children:!query||normalize(group.parent?.label||'Без специальности').includes(query)?group.children:group.children.filter(matches)}))
      .filter(group=>!query||group.children.length||normalize(group.parent?.label||'Без специальности').includes(query));
    shown.sort((a,b)=>!a.parent?1:!b.parent?-1:a.parent.label.localeCompare(b.parent.label,'ru'));
    if(editable)content.append(lazyEditor('Добавить специальность',null,'specialty'));
    else content.append(el('p',{className:'table-note'},'Справочник доступен для просмотра.'));
    content.append(pagination(shown.length,' групп'),el('p',{className:'table-note'},'Вариантов должностей: '+shown.reduce((sum,group)=>sum+group.children.length,0)));
    for(const group of shown.slice(offset,offset+50)) {
      const parent=group.parent;
      const summary=el('summary',{},el('span',{className:'wf-specialty-title'},parent?.label||'Без специальности'),
        el('span',{className:'wf-specialty-count'},String(group.children.length)),
        ...(!parent?[el('small',{},'Требуется сопоставление')]:!isActive(parent)?[el('small',{},'Специальность в архиве')]:[]));
      const section=el('details',{className:'wf-specialty-group',open:!!query||openSpecialties.has(group.key)},summary);
      let loaded=false;
      const fill=()=>{
        if(loaded)return;loaded=true;
        const body=el('div',{className:'wf-specialty-body'});
        if(parent&&editable&&!parent.system_value)body.append(lazyEditor('Редактировать специальность',parent,'specialty'));
        if(parent&&editable&&isActive(parent))body.append(lazyEditor('Добавить разряд / вариант должности',null,'profession',{specialty_code:parent.code}));
        else if(parent&&!isActive(parent))body.append(el('p',{className:'table-note'},'Новые назначения и добавление разрядов недоступны, пока специальность в архиве.'));
        if(!parent)body.append(el('p',{className:'table-note'},'Исходные должности сохранены. Для сопоставления выберите специальность в редакторе строки.'));
        if(!group.children.length)body.append(el('p',{className:'table-note'},query?'Подходящих вариантов нет.':'Разряды и варианты должностей пока не добавлены.'));
        if(group.children.length)body.append(catalogTable([
          ['Название',row=>row.label],['Разряд',row=>row.grade==null?'—':String(row.grade)],['Состояние',statusCell]
        ],[...group.children].sort((a,b)=>(a.grade??0)-(b.grade??0)||a.label.localeCompare(b.label,'ru')),editable,'profession'));
        section.append(body);
      };
      section.addEventListener('toggle',()=>{if(section.open){openSpecialties.add(group.key);fill();}else openSpecialties.delete(group.key);});
      if(section.open)fill();content.append(section);
    }
  }
  function render() {
    const selector=el('select',{'aria-label':'Справочник учёта'},...kinds.map(([value,label])=>el('option',{value},label)));selector.value=kind;
    selector.addEventListener('change',()=>{if(!canLeave()){selector.value=kind;return;}kind=selector.value;offset=0;search='';dirty=false;discardedDirty=false;render();});
    const query=el('input',{type:'search',value:search,placeholder:'Поиск по названию','aria-label':'Поиск по справочнику'});
    query.addEventListener('change',()=>{if(!canLeave()){query.value=search;return;}search=query.value;offset=0;dirty=false;discardedDirty=false;render();});
    const reload=el('button',{className:'secondary-button',type:'button',onclick:()=>{if(canLeave())load(kind,true);}},'Обновить');
    const content=document.createDocumentFragment();
    const heading=document.getElementById('catalog-rail')?el('h2',{className:'wf-catalog-title'},kinds.find(([value])=>value===kind)[1]):selector;
    content.append(el('div',{className:'wf-toolbar panel'},heading,query,reload));
    const editable=reference.permissions.catalog&&!fixed.has(kind);
    if(kind==='profession'){renderProfessions(content,editable);panel.replaceChildren(content);return;}
    let rows=kind==='organization'?reference.organizations:kind==='place'?reference.places:kind==='schedule'?reference.rotation_schedules:reference.catalog.filter(row=>row.kind===kind);
    rows=rows.filter(row=>(row.name||row.label).toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru')));
    if(editable)content.append(lazyEditor('Добавить значение',null,kind));
    else content.append(el('p',{className:'table-note'},fixed.has(kind)?'Перечень закреплён правилами учёта.':'Справочник доступен для просмотра.'));
    content.append(pagination(rows.length));
    const columns=[['Название',row=>row.name||row.label]];
    if(kind==='schedule')columns.push(['На участке, дней',row=>row.onsite_days??'—'],['МО, дней',row=>row.leave_days??'—'],
      ['До заезда, дней',row=>row.travel_days??'—'],['Расчёт',row=>row.needs_review?'Требует уточнения':'Доступен']);
    if(kind==='place')columns.push(['Адрес',row=>row.address||'—'],['Мест',row=>row.capacity??'—']);
    columns.push(['Состояние',statusCell]);
    content.append(catalogTable(columns,rows.slice(offset,offset+pageSize),editable));
    panel.replaceChildren(content);
  }
  window.addEventListener('beforeunload',event=>{if(busy||(dirty&&visible())){event.preventDefault();event.returnValue='';}});
  window.workforceCatalogs={load,canLeave,discard,getKind:()=>kind,kinds:kinds.map(row=>row.slice())};
})();
