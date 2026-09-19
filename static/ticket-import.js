(() => {
  'use strict';
  const root=document.querySelector('.app-shell');
  if(!root||!['super_admin','admin','rotation','recruitment'].includes(root.dataset.role))return;
  const host=document.querySelector('#wf-list-workspace');if(!host)return;
  const E=(tag,attrs={},...children)=>{const n=document.createElement(tag);for(const [k,v] of Object.entries(attrs)){if(k.startsWith('on'))n.addEventListener(k.slice(2),v);else if(k in n)n[k]=v;else n.setAttribute(k,v);}for(const child of children.flat())if(child!=null)n.append(child);return n;};
  const button=(label,fn)=>E('button',{type:'button',className:'secondary-button',onclick:fn},label);
  async function api(path,options={}){
    const headers={...options.headers};if(options.body&&!(options.body instanceof FormData)){headers['Content-Type']='application/json';options.body=JSON.stringify(options.body);}
    if(options.method&&options.method!=='GET')headers['X-CSRF-Token']=root.dataset.csrf;
    const response=await fetch('/api/workforce/tickets/'+path,{...options,headers});let data;
    try{data=await response.json();}catch{throw Error('Не удалось получить ответ сервера.');}
    if(!response.ok)throw Error(data.error||'Не удалось выполнить действие.');return data;
  }
  const items=new Map(),removed=new Set();let uploading=false,saving=false,draftContext=null;
  const status=E('p',{role:'status',className:'ticket-import-status'});
  const panel=E('dialog',{className:'ticket-import-panel','aria-label':'Загрузка и проверка билетов'});
  const open=button('Билеты',()=>show());
  open.classList.add('ticket-import-toggle');open.setAttribute('aria-label','Билеты');
  open.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" aria-hidden="true"><path d="M4 5h16v5a2 2 0 0 0 0 4v5H4v-5a2 2 0 0 0 0-4V5Z"/><path d="M9 5v3m0 2v4m0 2v3"/></svg>';
  open.title='Загрузить и проверить билеты';open.setAttribute('aria-expanded','false');document.querySelector('#wf-heading-actions').append(open);
  document.body.append(panel);
  const year=E('input',{type:'number',min:2000,max:2100,placeholder:'Не подставлять','aria-label':'Год для дат без года'});
  const file=E('input',{type:'file',multiple:true,accept:'.pdf,.jpg,.jpeg,.jfif,.png',hidden:true});
  const drop=E('div',{className:'ticket-dropzone',tabIndex:0,role:'button','aria-label':'Загрузить билеты PDF или фото',onclick:()=>file.click(),onkeydown:e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();file.click();}}},
    E('strong',{},'Перетащите билеты сюда'),E('span',{},'PDF, JPEG/JFIF, PNG · несколько файлов · до 12 МБ на файл'));
  drop.addEventListener('dragover',e=>{e.preventDefault();drop.classList.add('dragover');});
  drop.addEventListener('dragleave',()=>drop.classList.remove('dragover'));
  drop.addEventListener('drop',e=>{e.preventDefault();drop.classList.remove('dragover');upload([...e.dataTransfer.files]);});
  file.onchange=()=>{upload([...file.files]);file.value='';};
  const yearLabel=E('label',{},'Год для дат без года',year);
  const table=E('table',{className:'ticket-review-table'},E('thead',{},E('tr',{},...['✓','Файл / пассажир','Сотрудник','Заезд / выезд','Маршрут и реквизиты','Проверка'].map(t=>E('th',{scope:'col'},t)))));
  const body=E('tbody');table.append(body);
  const count=E('span');const confirm=button('Подтвердить выбранные',save);
  const selectAll=E('input',{type:'checkbox','aria-label':'Выбрать до 50 обработанных билетов',onchange:()=>{let selected=0;for(const item of items.values())if(item.checkbox)item.checkbox.checked=selectAll.checked&&selected++<50;summary();}});
  const tools=E('div',{className:'ticket-import-tools'},E('label',{},selectAll,'Выбрать до 50 обработанных'),count,confirm,button('Обновить список',load));
  const geography=E('details',{className:'ticket-geography'},E('summary',{},'Города и транспортные пункты проектов'));
  const geoBody=E('div');geography.append(geoBody);geography.addEventListener('toggle',()=>{if(geography.open)loadGeography();});
  const introduction=E('p');
  panel.append(E('div',{className:'ticket-import-heading'},E('h2',{},'Билеты: распознавание и подтверждение'),button('Закрыть',()=>{if(!saving)panel.close();})),
    introduction,
    E('div',{className:'ticket-upload-controls'},drop,yearLabel),file,geography,status,tools,E('div',{className:'ticket-review-scroll'},table));
  function show(context=null){
    if(panel.open)return;
    draftContext=context;items.clear();body.replaceChildren();selectAll.checked=false;status.textContent='';
    confirm.textContent=context?'Использовать для смены состояния':'Подтвердить выбранные';
    introduction.textContent=context
      ?'Проверьте сотрудника и все участки поездки. Подготовленные билеты сохранятся вместе со сменой состояния.'
      :'После обработки проверьте сотрудника, направление и все участки поездки. До подтверждения данные сотрудников не меняются.';
    geography.hidden=Boolean(context);open.setAttribute('aria-expanded','true');panel.showModal();summary();load();
  }
  panel.addEventListener('cancel',event=>{if(saving)event.preventDefault();});
  panel.addEventListener('close',()=>open.setAttribute('aria-expanded','false'));
  window.WorkforceTicketImport={open:show};
  function summary(){count.textContent='Файлов: '+items.size+' · Выбрано: '+[...items.values()].filter(i=>i.checkbox?.checked).length;confirm.disabled=saving||![...items.values()].some(i=>i.checkbox?.checked&&!i.deleting);}
  summary();
  function input(value,label,type='text'){return E('input',{type,value:value||'','aria-label':label});}
  const birthLabel=value=>value?' · ДР '+value.split('-').reverse().join('.'):'';
  function select(values,value,label){const el=E('select',{'aria-label':label});for(const [v,t] of values)el.append(E('option',{value:v},t));el.value=value||'';return el;}
  function render(data){
    if(removed.has(data.id))return;
    let item=items.get(data.id);if(item?.ticket||item?.deleting)return;
    if(!item){item={id:data.id,tr:E('tr')};items.set(data.id,item);body.prepend(item.tr);}
    item.state=data.state;item.filename=data.filename;item.tr.replaceChildren();
    const link=data.id.startsWith('error-')?E('span',{},data.filename):E('a',{href:'/api/workforce/tickets/uploads/'+data.id+'/source',target:'_blank',rel:'noopener'},data.filename);
    const remove=button('Удалить',()=>removeTicket(item));remove.classList.add('ticket-delete');remove.title='Удалить из распознавания';remove.setAttribute('aria-label','Удалить билет '+data.filename);
    const fileActions=E('div',{className:'ticket-file-actions'},link,remove);
    if(data.state!=='ready'||!data.ticket){
      item.tr.append(E('td',{},'—'),E('td',{},fileActions),E('td',{colSpan:4},data.error||({pending:'В очереди',running:'Распознаётся…',ready:'Загружаются результаты…',applied:'Сохранён'}[data.state]||data.state)));summary();return;
    }
    const preset=draftContext?.presets?.find(p=>p.job_id===data.id);
    const ticket=preset||data.ticket;
    item.ticket=structuredClone(ticket);item.checkbox=E('input',{type:'checkbox',checked:Boolean(preset),'aria-label':'Выбрать билет '+data.filename,onchange:summary});
    item.passenger=input(ticket.passenger,'ФИО в билете '+data.filename);
    item.number=input(ticket.ticket_number,'Номер билета '+data.filename);
    item.transport=select([['','Вид транспорта'],['air','Авиа'],['rail','ЖД']],ticket.transport,'Транспорт '+data.filename);
    const candidates=data.candidates||[];
    item.direction=select([['','Выберите'],['arrival','Заезд'],['departure','Выезд']],draftContext?.direction||(candidates[0]?.recommended?candidates[0].direction:''),'Направление '+data.filename);
    item.direction.disabled=Boolean(draftContext);
    const people=draftContext?.people||candidates;
    const recommended=candidates.find(c=>c.recommended&&people.some(p=>Number(p.id)===Number(c.id)));
    item.worker=select([['','Выберите сотрудника'],...people.map(c=>[String(c.id),c.full_name+' · '+(c.department||'')+' · '+(c.personnel_no||'без таб. №')+birthLabel(c.birth_date)])],preset?String(preset.worker_id):(recommended?String(recommended.id):''),'Сотрудник '+data.filename);
    const reason=E('small',{},candidates[0]?.reasons.join('. ')||'Совпадение не найдено. Найдите сотрудника вручную.');
    item.worker.onchange=()=>{const candidate=candidates.find(c=>String(c.id)===item.worker.value);reason.textContent=candidate?.reasons.join('. ')||'Сотрудник выбран вручную';if(!draftContext)item.direction.value=candidate?.direction||'';};
    const search=input('','Поиск другого сотрудника '+data.filename);search.placeholder='ФИО или табельный';
    const searchButton=button('Найти',async()=>{try{const response=await api('workers?q='+encodeURIComponent(search.value));item.worker.replaceChildren(E('option',{value:''},'Выберите сотрудника'),...response.rows.map(c=>E('option',{value:String(c.id)},c.full_name+' · '+c.department+' · '+c.personnel_no+birthLabel(c.birth_date))));item.direction.value='';reason.textContent='Результаты ручного поиска: '+response.rows.length;}catch(e){status.textContent=e.message;}});
    const segments=E('div',{className:'ticket-segments'});item.segments=[];
    function addSegment(s={}){
      const controls={};const box=E('fieldset',{},E('legend',{},'Участок маршрута'));
      for(const [key,label,type] of [['origin','Откуда'],['destination','Куда'],['departure_date','Отправление','date'],['departure_time','Время отправления','time'],['arrival_date','Прибытие','date'],['arrival_time','Время прибытия','time'],['flight','Рейс / поезд'],['coach','Вагон'],['seat','Место'],['timezone_note','Часовой пояс']]){
        controls[key]=input(s[key],label+' '+data.filename,type||'text');box.append(E('label',{},label,controls[key]));
      }
      if(s.date_note)box.append(E('small',{},'В билете: '+s.date_note));
      if(s.origin_original||s.destination_original)box.append(E('small',{},'Исходный маршрут: '+(s.origin_original||s.origin||'?')+' → '+(s.destination_original||s.destination||'?')));
      box.append(button('Удалить участок',()=>{if(item.segments.length===1)return;item.segments=item.segments.filter(x=>x!==controls);box.remove();}));
      item.segments.push(controls);segments.append(box);
    }
    ticket.segments.forEach(addSegment);
    const route=E('details',{},E('summary',{},ticket.segments.map(s=>(s.origin||'?')+' → '+(s.destination||'?')).join(' · ')),segments,button('Добавить участок',()=>addSegment()));
    item.tr.append(E('td',{},item.checkbox),E('td',{},fileActions,item.passenger,data.ticket.birth_date?E('small',{},'Дата рождения в билете: '+data.ticket.birth_date.split('-').reverse().join('.')):null),E('td',{},item.worker,draftContext?E('small',{},'Сотрудники, выбранные для смены состояния'):E('div',{className:'ticket-worker-search'},search,searchButton),reason),
      E('td',{},item.direction),E('td',{},item.transport,item.number,route),E('td',{},...(data.ticket.warnings||[]).map(w=>E('p',{},w))));summary();
    [...item.tr.cells].forEach((cell,index)=>cell.dataset.label=['Выбрать','Файл / пассажир','Сотрудник','Заезд / выезд','Маршрут и реквизиты','Проверка'][index]);
  }
  async function poll(id){
    for(;;){while(items.get(id)?.deleting)await new Promise(resolve=>setTimeout(resolve,100));if(removed.has(id))return;let data;try{data=await api('uploads/'+id);}catch(error){while(items.get(id)?.deleting)await new Promise(resolve=>setTimeout(resolve,100));if(removed.has(id))return;throw error;}if(removed.has(id))return;render(data);if(!['pending','running'].includes(data.state))return;await new Promise(resolve=>setTimeout(resolve,1800));}
  }
  async function removeTicket(item){
    if(saving||item.deleting)return;
    const message='Удалить «'+item.filename+'» из списка распознавания?'+(item.state==='applied'?' Сохранённая поездка и история останутся в карточке сотрудника.':' Результат распознавания больше нельзя будет подтвердить.');
    if(!window.confirm(message))return;
    item.deleting=true;item.tr.inert=true;summary();
    try{
      if(!item.id.startsWith('error-'))await api('uploads/'+item.id,{method:'DELETE',body:{state:item.state}});
      removed.add(item.id);items.delete(item.id);item.tr.remove();status.textContent='Билет удалён из распознавания.';
    }catch(error){status.textContent=error.message;}
    finally{item.deleting=false;item.tr.inert=false;summary();}
  }
  async function upload(files){
    if(uploading){status.textContent='Дождитесь обработки текущей партии.';return;}
    if(!files.length)return;if(!year.reportValidity())return;
    uploading=true;year.disabled=true;let completed=0;const queue=[...files];
    try{
      await Promise.all(Array.from({length:Math.min(3,files.length)},async()=>{while(queue.length){
        const source=queue.shift();status.textContent='Обработано '+completed+' из '+files.length+' · '+source.name;
        try{const form=new FormData();form.append('file',source);form.append('travel_year',year.value);const data=await api('uploads',{method:'POST',body:form});await poll(data.id);}
        catch(e){const key='error-'+Math.random();render({id:key,filename:source.name,state:'failed',error:e.message});}
        completed++;
      }}));
      status.textContent='Обработка завершена. Проверьте результаты и отметьте билеты для сохранения.';
    }finally{uploading=false;year.disabled=false;}
  }
  async function load(){
    try{
      const data=await api('uploads');const queue=[];
      for(const row of data.rows){render(row);if(['pending','running','ready'].includes(row.state)&&!items.get(row.id)?.ticket)queue.push(row.id);}
      await Promise.all(Array.from({length:Math.min(3,queue.length)},async()=>{while(queue.length){try{await poll(queue.shift());}catch(e){status.textContent=e.message;}}}));
    }
    catch(e){status.textContent=e.message;}
  }
  async function save(){
    const selected=[...items.values()].filter(i=>i.checkbox?.checked&&!i.deleting);if(!selected.length||saving)return;
    saving=true;panel.inert=true;confirm.disabled=true;
    try{
      const payload=selected.map(i=>({job_id:i.id,worker_id:Number(i.worker.value),direction:i.direction.value,passenger:i.passenger.value,
        ticket_number:i.number.value,transport:i.transport.value,segments:i.segments.map(s=>Object.fromEntries(Object.entries(s).map(([key,field])=>[key,field.value])))}));
      if(payload.some(i=>!i.worker_id||!i.direction))throw Error('В каждой выбранной строке укажите сотрудника и заезд / выезд.');
      if(draftContext){
        if(payload.some(i=>!draftContext.people.some(p=>Number(p.id)===i.worker_id)))throw Error('Укажите сотрудника из выбранного состава.');
        if(new Set(payload.map(i=>i.worker_id)).size!==payload.length)throw Error('Для одного сотрудника выберите один билет. Пересадки добавьте участками маршрута.');
        const response=await api('drafts',{method:'POST',body:{items:payload}});
        await draftContext.onSelect(response.items);panel.close();return;
      }
      const response=await api('confirm',{method:'POST',body:{items:payload}});
      for(const id of response.saved){const item=items.get(id);item.ticket=null;item.checkbox=null;render({id,state:'applied',filename:selected.find(x=>x.id===id)?.tr.querySelector('a')?.textContent||'Билет'});}
      status.textContent='Сохранено билетов: '+response.saved.length+'. Поездки добавлены; фактическая явка не изменена.';
    }catch(e){status.textContent=e.message;}finally{saving=false;panel.inert=false;summary();}
  }
  async function loadGeography(){
    try{
      const data=await api('geography');geoBody.replaceChildren(E('p',{},'Свяжите проект с пунктами прибытия. Варианты написания и коды аэропортов укажите через запятую.'));
      const rows=[];const list=E('div');geoBody.append(list);
      function add(link={}){
        const project=select([['','Проект'],...data.reference.filter(r=>r.kind==='project').map(r=>[r.code,r.label])],link.project_code,'Проект');
        const point=select([['','Город / станция'],...data.reference.filter(r=>r.kind==='travelpoint').map(r=>[r.code,r.label])],link.point_code,'Город или станция');
        const aliases=input((link.aliases||[]).join(', '),'Другие названия и коды');const row={project,point,aliases};rows.push(row);
        const box=E('div',{className:'ticket-geography-row'},project,point,aliases,button('Удалить',()=>{rows.splice(rows.indexOf(row),1);box.remove();}));list.append(box);
        if(!data.editable)for(const field of box.querySelectorAll('input,select,button'))field.disabled=true;
      }
      data.links.forEach(add);
      if(data.editable)geoBody.append(button('Добавить связь',()=>add()),button('Сохранить связи',async()=>{try{await api('geography',{method:'PUT',body:{token:data.token,links:rows.map(r=>({project_code:r.project.value,point_code:r.point.value,aliases:r.aliases.value.split(',').map(v=>v.trim()).filter(Boolean)}))}});status.textContent='Связи сохранены. Для новых сопоставлений будут учтены города проектов.';await loadGeography();}catch(e){status.textContent=e.message;}}));
    }catch(e){geoBody.textContent=e.message;}
  }
  function route(){const relevant=/^#workforce\/(rotation|recruitment)/.test(location.hash);open.hidden=!relevant||root.dataset.role==='recruitment';if(!relevant&&panel.open&&!saving)panel.close();}
  // Menu navigation uses pushState, which does not emit hashchange.
  addEventListener('workforce:sectionchange',route);
  addEventListener('hashchange',route);route();
})();
