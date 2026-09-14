(() => {
  'use strict';
  const MF = window.MultiFilter;
  const host = document.getElementById('view-verification');
  if (!host) return;
  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    Object.entries(attrs).forEach(([key,value]) => key in node ? node[key]=value : node.setAttribute(key,value));
    node.append(...children); return node;
  };
  const option = (value,text) => el('option',{value},text);
  const labels = {unverified:'Не проверено',verified:'Подтверждено',changed:'Изменено после проверки'};
  const day = el('input',{type:'date',required:true,'aria-label':'Дата верификации'});
  const shift = el('select',{'aria-label':'Смена верификации'},option('all','Обе смены'),option('1 смена','День'),option('2 смена','Ночь'));
  const department = el('select',{'aria-label':'СМУ верификации'});
  const contractor = el('select',{'aria-label':'Подрядчик верификации'});
  const pps = el('select',{'aria-label':'ППС верификации'});
  const statusFilter = el('select',{'aria-label':'Статус верификации'},option('pending','Требуют проверки'),option('','Все статусы'),
    ...Object.entries(labels).map(([key,label])=>option(key,label)));
  const search = el('input',{type:'search',placeholder:'Позиция, ФИО или табельный номер','aria-label':'Поиск для верификации',maxLength:300});
  const refresh = el('button',{type:'button',className:'secondary-button'},'Обновить');
  const message = el('p',{role:'status',className:'verification-message'});
  const totals = el('div',{className:'verification-totals'});
  const groups = el('div',{className:'verification-groups'});
  const field = (title,input) => el('label',{},title,input);
  const controls = el('fieldset',{className:'verification-controls'},field('Дата',day),field('Смена',shift),
    field('СМУ',department),field('Компания-подрядчик',contractor),field('ППС',pps),field('Статус',statusFilter),field('Поиск',search),refresh);
  const workspace = el('div',{className:'verification-workspace'},
    el('div',{className:'page-heading'},el('h1',{},'Верификация по позициям')),
    el('p',{className:'table-note'},'Проверьте состав сотрудников на каждой позиции. Подтверждение действует для выбранной даты, смены и текущих данных назначения. Изменения требуют повторной проверки.'),
    controls,message,totals,groups);
  host.append(workspace);
  [shift,department,contractor,pps,statusFilter].forEach(input => MF.enable(input, input === shift ? 'all' : ''));
  day.value=document.getElementById('staffing-date').value;
  let rows = [], busy = false, sequence = 0;
  const groupKey = row => row.subobject_id + ':' + row.shift;
  const norm = value => String(value || '').toLocaleLowerCase('ru').replace(/ё/g,'е');
  function tell(text,error=false) { message.textContent=text;message.classList.toggle('error-text',error); }
  function options(input,values,title) {
    const selected=MF.get(input);
    const choices=[...new Set(values.map(v=>v||''))].sort((a,b)=>a.localeCompare(b,'ru',{numeric:true}));
    input.replaceChildren(option('',title),...choices.map(value=>option(JSON.stringify(value),value||'Не указано')));
    MF.set(input,selected);
  }
  function selected(row,input,key) { return MF.matches(MF.get(input), JSON.stringify(row[key]||'')); }
  function filtered() {
    const words=norm(search.value).trim().split(/\s+/).filter(Boolean);
    return rows.filter(row=>MF.matches(MF.get(shift),row.shift)&&selected(row,department,'department')&&selected(row,contractor,'contractor')&&selected(row,pps,'pps')&&
      words.every(word=>norm([row.object_name,row.subobject_name,row.full_name,row.personnel_no,row.crew_name,row.linear_itr].join(' ')).includes(word)));
  }
  function render() {
    const opened=new Set([...groups.querySelectorAll('details[open]')].map(node=>node.dataset.key));
    const base=filtered(),counts=Object.fromEntries(Object.keys(labels).map(key=>[key,base.filter(r=>r.verification_status===key).length]));
    totals.replaceChildren(...[['Назначений',base.length],...Object.entries(labels).map(([key,label])=>[label,counts[key]])]
      .map(([label,count])=>el('span',{},label+': ',el('strong',{},String(count)))));
    const visible=base.filter(row=>!MF.get(statusFilter) || MF.values(MF.get(statusFilter)).some(status => status==='pending'?row.verification_status!=='verified':row.verification_status===status));
    const positions=new Map();
    visible.forEach(row=>{const key=groupKey(row);if(!positions.has(key))positions.set(key,[]);positions.get(key).push(row);});
    groups.replaceChildren();
    if(!positions.size){groups.append(el('p',{className:'empty-state'},'Нет назначений по выбранным условиям.'));return;}
    positions.forEach((people,key)=>{
      const first=people[0],pending=people.filter(r=>r.verification_status!=='verified');
      const detail=el('details',{className:'verification-position',open:opened.has(key)||positions.size===1,'data-key':key});
      detail.append(el('summary',{},el('strong',{},first.object_name+' / '+first.subobject_name),
        el('span',{},(first.shift==='2 смена'?'Ночь':'День')+' · показано '+people.length+' · требуют проверки '+pending.length)));
      const verify=el('button',{type:'button',className:'primary-button',disabled:busy||!pending.length},'Подтвердить показанных ('+pending.length+')');
      verify.addEventListener('click',()=>save(pending,'verify'));
      detail.append(el('div',{className:'verification-position-actions'},verify));
      const list=el('ul',{className:'verification-people'});
      people.forEach(row=>{
        const info=el('div',{},el('strong',{},row.full_name),el('small',{},'Таб. № '+row.personnel_no+' · '+(row.category||'Без ГДЛР')),
          el('small',{},[row.department,row.pps,row.contractor].filter(Boolean).join(' · ')),
          el('small',{},'Бригада: '+(row.crew_name||'не указана')+' · ИТР: '+(row.linear_itr||'не указан')));
        if(row.attendance_status!=='Явка')info.append(el('span',{className:'verification-warning'},'Неявка: '+row.attendance_status));
        if(!row.active)info.append(el('span',{className:'verification-warning'},'Сотрудник отключён'));
        if(row.performed_work)info.append(el('small',{},'Работы: '+row.performed_work));
        const state=el('div',{className:'verification-result'},el('span',{className:'verification-badge '+row.verification_status},labels[row.verification_status]));
        if(row.verified_at)state.append(el('small',{},row.verified_by+' · '+new Date(row.verified_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})+' МСК'));
        const action=row.verification_status==='verified'?'clear':'verify';
        const button=el('button',{type:'button',className:action==='verify'?'primary-button':'secondary-button',disabled:busy},action==='verify'?'Подтвердить':'Снять подтверждение');
        button.setAttribute('aria-label',(action==='verify'?'Подтвердить: ':'Снять подтверждение: ')+row.full_name);
        button.addEventListener('click',()=>save([row],action));state.append(button);list.append(el('li',{},info,state));
      });
      detail.append(list);groups.append(detail);
    });
  }
  async function api(method,body) {
    const response=await fetch('/api/staffing/verification'+(method==='GET'?'?'+new URLSearchParams({date:day.value,shift:'all'}):''),
      {method,cache:'no-store',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('main[data-csrf]').dataset.csrf},
        ...(body?{body:JSON.stringify(body)}:{})});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось выполнить проверку.');return data;
  }
  async function read() {
    const data=await api('GET');rows=data.rows;
    options(department,rows.map(r=>r.department),'Все СМУ');options(contractor,rows.map(r=>r.contractor),'Все подрядчики');options(pps,rows.map(r=>r.pps),'Все ППС');
  }
  async function load() {
    if(busy)return;const current=++sequence;busy=true;controls.disabled=true;render();tell('Загрузка назначений…');
    try{await read();if(current===sequence)tell('Показана расстановка на '+day.value.split('-').reverse().join('.')+'.');}
    catch(error){rows=[];tell(error.message,true);}
    finally{busy=false;controls.disabled=false;render();}
  }
  async function save(people,action) {
    if(busy||!people.length)return;
    busy=true;controls.disabled=true;render();tell('Сохранение проверки…');
    let failure='';
    try{await api('POST',{date:day.value,shift:'all',action,assignment_ids:people.map(r=>r.assignment_id),
      expected_tokens:Object.fromEntries(people.map(r=>[r.assignment_id,r.expected_token]))});}
    catch(error){failure=error.message;}
    try{await read();tell(failure||(action==='verify'?'Подтверждено назначений: ':'Снято подтверждений: ')+people.length,!!failure);}
    catch(error){rows=[];tell((failure?failure+' ':'')+'Обновите список для проверки сохранения. '+error.message,true);}
    finally{busy=false;controls.disabled=false;render();}
  }
  window.placementVerificationScreen = { load };
  [day,shift].forEach(input=>input.addEventListener('change',load));
  [department,contractor,pps,statusFilter].forEach(input=>input.addEventListener('change',render));
  search.addEventListener('input',render);refresh.addEventListener('click',load);
})();
