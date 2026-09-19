(() => {
  'use strict';
  const root=document.querySelector('.app-shell'), panel=document.getElementById('report-closure');if(!panel)return;
  const E=(tag,attrs={},...children)=>{const n=document.createElement(tag);for(const [k,v]of Object.entries(attrs)){if(k.startsWith('on'))n.addEventListener(k.slice(2),v);else if(k in n)n[k]=v;else n.setAttribute(k,v);}n.append(...children.flat());return n;};
  const canWrite=['admin','super_admin'].includes(root.dataset.role), content=document.getElementById('report-closure-content');
  let current=null,seq=0,initialized=false,busy=false;
  const day=E('input',{type:'date',value:document.getElementById('placement-report-date').value,required:true,onchange:load});
  const scope=E('select',{'aria-label':'СМУ закрываемого отчёта',onchange:load},E('option',{value:''},'Выберите СМУ'));
  const status=E('p',{role:'status','aria-live':'polite'}), preview=E('div'), history=E('div');
  const check=E('button',{type:'button',disabled:true,onclick:()=>save('check')},'Подтвердить проверку');
  const close=E('button',{type:'button',disabled:true,onclick:()=>save('close')},'Закрыть день');
  const reason=E('input',{type:'text',maxLength:2000,placeholder:'Что исправлено и почему'});
  const reasonLabel=E('label',{},'Причина новой версии',reason);reasonLabel.hidden=true;
  const toolbar=E('div',{className:'wf-operations-toolbar'},E('label',{},'Дата',day),E('label',{},'СМУ',scope),E('button',{type:'button',onclick:load},'Обновить'));
  content.append(toolbar,E('p',{className:'wf-forecast-note'},'Сохраняется полный отчёт выбранного СМУ за день. Фильтры ППС, категории и автора ниже не сокращают сохраняемую версию.'),status,preview);
  if(canWrite)content.append(E('div',{className:'wf-operations-toolbar'},check,reasonLabel,close));
  content.append(history);
  async function api(url,options={}){const response=await fetch(url,{...options,cache:'no-store',headers:{'Content-Type':'application/json','X-CSRF-Token':root.dataset.csrf}});return window.readApiResponse(response,'Не удалось загрузить версию отчёта.');}
  async function load(){
    if(busy)return;const request=++seq;current=null;check.disabled=close.disabled=true;preview.replaceChildren();history.replaceChildren();reasonLabel.hidden=true;
    if(!day.value||!scope.value){status.textContent='Выберите дату и СМУ.';return;}
    status.textContent='Загрузка…';
    try{const data=await api('/api/placement-report/closure?'+new URLSearchParams({date:day.value,scope:'department',department:scope.value}));if(request!==seq)return;current=data;render();}
    catch(error){if(request===seq)status.textContent=error.message;}
  }
  function totals(report){return E('div',{className:'wf-operations-counts'},...Object.entries({total:'Всего',assigned:'Расставлены',unassigned:'Не расставлены',absent:'Неявка'}).map(([k,t])=>E('strong',{},t+': '+report.totals[k])));}
  function people(report){
    const list=E('div'), rows=(report.groups||[]).flatMap(g=>g.people||[]);let offset=0;
    const draw=()=>{list.replaceChildren(...rows.slice(offset,offset+50).map(p=>E('p',{},E('button',{type:'button',className:'wf-person-link',onclick:()=>window.openWorkforcePerson(p.id,report.date)},p.full_name),' · '+[p.department,p.pps,p.category,...(p.assignments||[]).map(a=>a.shift+': '+a.object_name+'/'+a.subobject_name)].filter(Boolean).join(' · '))),E('div',{className:'wf-operations-page'},E('button',{type:'button',disabled:offset===0,onclick:()=>{offset-=50;draw();}},'←'),E('span',{},'Страница '+(offset/50+1)+' из '+Math.max(1,Math.ceil(rows.length/50))),E('button',{type:'button',disabled:offset+50>=rows.length,onclick:()=>{offset+=50;draw();}},'→')));};draw();return list;
  }
  function render(){
    const data=current;
    status.textContent=data.divergent?'После закрытия данные изменились. Сохранённая версия остаётся прежней.':data.status==='closed'?'День закрыт · версия '+data.latest_version.version:data.status==='checked'?'Проверка подтверждена. Можно закрыть день.':'Черновик · проверьте состав и назначения.';
    preview.replaceChildren(totals(data.current),E('details',{},E('summary',{},'Проверить сотрудников текущего отчёта'),people(data.current)));
    check.disabled=busy||data.ready_to_close;close.disabled=busy||!data.ready_to_close||(!data.divergent&&!!data.latest_version);
    close.textContent=data.latest_version?'Сохранить исправленную версию':'Закрыть день';reasonLabel.hidden=!(data.latest_version&&data.divergent);
    check.hidden=close.hidden=!!data.latest_version&&!data.divergent;
    history.replaceChildren(E('h3',{},'Сохранённые версии'));
    if(!data.versions.length)history.append(E('p',{},'День ещё не закрывался.'));
    for(const version of data.versions){
      const details=E('details',{},E('summary',{},`Версия ${version.version} · ${new Date(version.created_at).toLocaleString('ru-RU')} · ${version.actor.full_name}`));
      const target=E('div'), base='/api/placement-report/closure/versions/'+version.id;
      details.append(E('p',{},version.correction_reason||'Первичное закрытие'),E('div',{className:'wf-operations-page'},E('a',{href:base+'/pdf',download:''},'Скачать PDF'),E('a',{href:base+'/export',download:''},'Скачать данные JSON')),target);
      details.addEventListener('toggle',async()=>{if(!details.open||target.childNodes.length)return;target.textContent='Загрузка…';try{const saved=await api(base);target.replaceChildren(totals(saved.report),people(saved.report));}catch(error){target.textContent=error.message;}});
      history.append(details);
    }
  }
  async function save(kind){
    if(busy||!current)return;if(kind==='close'&&current.latest_version&&!reason.value.trim()){status.textContent='Укажите причину исправления.';reason.focus();return;}
    busy=true;check.disabled=close.disabled=true;day.disabled=scope.disabled=true;
    const payload={date:current.date,scope:current.scope,expected_token:current.expected_token,idempotency_key:crypto.randomUUID()};
    if(kind==='close'&&current.latest_version)payload.reason=reason.value.trim();
    let error;
    try{await api('/api/placement-report/closure/'+kind,{method:'POST',body:JSON.stringify(payload)});reason.value='';}
    catch(err){error=err.message;}
    finally{busy=false;day.disabled=scope.disabled=false;await load();if(error)status.textContent=error;}
  }
  panel.addEventListener('toggle',async()=>{if(!panel.open||initialized)return;initialized=true;try{const data=await api('/api/placement-report/closure/scopes');scope.append(...data.departments.map(name=>E('option',{value:name},name)));await load();}catch(error){status.textContent=error.message;initialized=false;}});
})();
