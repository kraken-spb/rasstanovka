(() => {
  'use strict';
  const root=document.querySelector('.app-shell');
  let panel, reference, kind='organization', search='', offset=0, busy=false, dirty=false;
  const kinds=[['organization','Работодатели'],['citizenship','Гражданство'],['place','Места ПВП'],['schedule','Графики вахтования'],
    ['document','Документы'],['check','Проверки оформления'],['project','Проекты'],['employment','Статус сотрудника'],
    ['stage','Присутствие'],['basis','Основание поездки'],['result','Результат поездки'],['docstate','Состояние документа'],['checkstate','Состояние проверки']];
  const fixed=new Set(['employment','stage','basis','result','docstate','checkstate']);
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
    const response=await fetch('/api/workforce/'+path,{...options,cache:'no-store',headers:{'Content-Type':'application/json','X-CSRF-Token':root.dataset.csrf}});
    const body=await response.json();if(!response.ok)throw new Error(body.error||'Не удалось сохранить справочник.');return body;
  }
  const canLeave=()=>!busy&&(!dirty||window.confirm('В справочнике есть несохранённые изменения. Закрыть их?'));
  async function load() {
    panel=document.getElementById('workforce-catalog-panel');if(!panel)return;
    try {reference=await api('reference');dirty=false;render();}
    catch(error){panel.replaceChildren(el('p',{className:'error-text',role:'alert'},error.message));}
  }
  function editor(row) {
    const form=el('form',{className:'wf-form'}), inputs={};let attempt;
    const fields=kind==='schedule' ? [['name','Название','text'],['onsite_days','Дней на участке','number'],['leave_days','Дней МО','number'],['travel_days','Дней до заезда после МО','number']] : [['label','Название','text']];
    if(kind==='place')fields.push(['address','Адрес','text'],['capacity','Вместимость','number']);
    for(const [key,title,type] of fields) {
      const value=key==='label' ? row?.label||row?.name||'' : row?.[key]??'';
      const input=el('input',{type,value,required:!['address','capacity'].includes(key),maxLength:200});
      if(type==='number'){input.min=key==='leave_days'||key==='capacity'?'0':'1';input.step='1';}
      inputs[key]=input;form.append(el('label',{},title,input));
    }
    const active=el('input',{type:'checkbox',checked:row?.active!==false});
    form.append(el('label',{className:'check-label'},active,'Действующее значение'));
    const reason=el('textarea',{required:true,maxLength:10000});form.append(el('label',{className:'wf-wide'},'Основание изменения',reason));
    const message=el('p',{className:'error-text wf-wide',role:'alert'});
    const submit=el('button',{type:'submit',className:'primary-button wf-wide'},row?'Сохранить':'Добавить');form.append(submit,message);
    form.addEventListener('input',()=>{dirty=true;});
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(busy||!form.reportValidity())return;
      const body={active:active.checked,reason:reason.value};
      for(const [key,input] of Object.entries(inputs))body[key]=input.type==='number'?(input.value===''?null:Number(input.value)):input.value;
      if(row)body.token=row.edit_token;
      const fingerprint=JSON.stringify(body);if(!attempt||attempt.fingerprint!==fingerprint)attempt={fingerprint,key:crypto.randomUUID()};
      body.request_key=attempt.key;busy=true;submit.disabled=true;message.textContent='';
      try {
        const id=row&&(row.id||row.code);
        const path=kind==='schedule'?'rotation-schedules':'catalog/'+kind;
        await api(path+(id?'/'+encodeURIComponent(id):''),{method:row?'PATCH':'POST',body:JSON.stringify(body)});
        dirty=false;window.workforceScreen?.invalidate();await load();
      } catch(error){message.textContent=error.message;}
      finally {busy=false;submit.disabled=false;}
    });
    return form;
  }
  function render() {
    const selector=el('select',{'aria-label':'Справочник учёта'},...kinds.map(([value,label])=>el('option',{value},label)));selector.value=kind;
    selector.addEventListener('change',()=>{if(!canLeave()){selector.value=kind;return;}kind=selector.value;offset=0;search='';dirty=false;render();});
    const query=el('input',{type:'search',value:search,placeholder:'Поиск по названию','aria-label':'Поиск по справочнику'});
    query.addEventListener('change',()=>{if(!canLeave())return;search=query.value;offset=0;dirty=false;render();});
    const reload=el('button',{className:'secondary-button',type:'button',onclick:()=>{if(canLeave())load();}},'Обновить');
    panel.replaceChildren(el('div',{className:'wf-toolbar panel'},selector,query,reload));
    let rows=kind==='organization'?reference.organizations:kind==='place'?reference.places:kind==='schedule'?reference.rotation_schedules:reference.catalog.filter(row=>row.kind===kind);
    rows=rows.filter(row=>(row.name||row.label).toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru')));
    const editable=reference.permissions.catalog&&!fixed.has(kind);
    if(editable)panel.append(el('details',{className:'wf-entry'},el('summary',{},'Добавить значение'),editor(null)));
    else panel.append(el('p',{className:'table-note'},fixed.has(kind)?'Перечень закреплён правилами учёта.':'Справочник доступен для просмотра.'));
    const nav=el('div',{className:'wf-list-heading'},el('span',{},`${rows.length?offset+1:0}–${Math.min(offset+50,rows.length)} из ${rows.length}`),
      el('div',{},el('button',{type:'button',className:'secondary-button',disabled:!offset,onclick:()=>{if(canLeave()){offset=Math.max(0,offset-50);dirty=false;render();}}},'←'),
      el('button',{type:'button',className:'secondary-button',disabled:offset+50>=rows.length,onclick:()=>{if(canLeave()){offset+=50;dirty=false;render();}}},'→')));
    panel.append(nav);
    for(const row of rows.slice(offset,offset+50)) {
      const block=el('article',{className:'wf-entry'},el('strong',{},row.name||row.label),el('small',{},row.active?'Действует':'Отключено'));
      if(kind==='schedule')block.append(el('p',{},row.needs_review?'Длительность не уточнена; автоматический расчёт недоступен.':`${row.onsite_days} дней на участке · ${row.leave_days} дней МО · ${row.travel_days} дней до заезда`));
      if(kind==='place')block.append(el('p',{},[row.address,row.capacity!=null?'Мест: '+row.capacity:''].filter(Boolean).join(' · ')));
      if(editable&&!row.system_value)block.append(el('details',{},el('summary',{},'Редактировать'),editor(row)));
      panel.append(block);
    }
  }
  window.addEventListener('beforeunload',event=>{if(dirty||busy){event.preventDefault();event.returnValue='';}});
  window.workforceCatalogs={load,canLeave};
})();
