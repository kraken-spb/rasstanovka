(() => {
  'use strict';
  const root=document.getElementById('view-divisions'); if(!root)return;
  const shell=document.querySelector('.app-shell');
  let data, search='',parent='',offset=0,busy=false,dirty=false;
  const E=(tag,props={},...children)=>{const node=document.createElement(tag);for(const [k,v] of Object.entries(props)){if(k.startsWith('on'))node.addEventListener(k.slice(2),v);else if(k in node)node[k]=v;else node.setAttribute(k,v);}children.flat().filter(v=>v!=null).forEach(v=>node.append(v));return node;};
  const status=E('p',{className:'save-status',role:'status'});
  const canLeave=()=>root.hidden||(!busy&&(!dirty||confirm('Закрыть несохранённые изменения подразделения?')));
  async function api(path='',options={}) {
    if(options.method)window.catalogData.invalidate();
    return window.readApiResponse(await fetch('/api/workforce/divisions'+path,{...options,cache:'no-store',headers:{'Content-Type':'application/json','X-CSRF-Token':shell.dataset.csrf}}),'Не удалось сохранить подразделение.');
  }
  async function load(refresh=false){busy=true;root.inert=true;try{data=await window.catalogData.get('/api/workforce/divisions',()=>api(),{refresh});dirty=false;render();}catch(error){status.textContent=error.message;root.append(status);}finally{busy=false;root.inert=false;}}
  function editor(row=null){
    const name=E('input',{value:row?.name||'',required:true,maxLength:500,'aria-label':'Название подразделения'});
    const pps=E('select',{'aria-label':'ППС подразделения'},E('option',{value:''},'ППС не указан'),...data.pps.filter(p=>p.active||p.id===row?.pps_id).map(p=>E('option',{value:String(p.id)},p.name)));pps.value=String(row?.pps_id||'');
    const active=E('input',{type:'checkbox',checked:row?.active??true});
    const notes=E('textarea',{value:row?.notes||'',rows:2,maxLength:5000,'aria-label':'Примечание'});
    const form=E('form',{className:'division-form'},E('label',{},'Название',name),E('label',{},'ППС',pps),E('label',{className:'check-label'},active,'Доступно'),E('label',{className:'division-notes'},'Примечание / источник',notes),E('div',{className:'division-actions'},E('button',{type:'submit',className:'primary-button'},'Сохранить'),E('button',{type:'button',className:'secondary-button',onclick:()=>{dirty=false;render();}},'Отмена')));
    form.addEventListener('input',()=>{dirty=true;});
    form.addEventListener('submit',async event=>{event.preventDefault();await mutate(row?'/'+row.id:'',row?'PATCH':'POST',{name:name.value,pps_id:pps.value?Number(pps.value):null,active:active.checked,notes:notes.value,...(row?{expected_token:row.edit_token}:{})});});
    return form;
  }
  async function mutate(path,method,body){if(busy)return;busy=true;root.inert=true;try{await api(path,{method,body:JSON.stringify(body)});dirty=false;data=await api();render();status.textContent='Справочник сохранён.';}catch(error){status.textContent=error.message;}finally{busy=false;root.inert=false;}}
  function render(){
    status.textContent='';
    const input=E('input',{type:'search',value:search,placeholder:'Название подразделения','aria-label':'Поиск подразделения'});
    const pps=E('select',{'aria-label':'Фильтр ППС'},E('option',{value:''},'Все ППС'),E('option',{value:'none'},'ППС не указан'),...data.pps.map(p=>E('option',{value:String(p.id)},p.name)));pps.value=parent;
    const body=E('tbody'),count=E('span',{className:'table-note'}),prev=E('button',{type:'button',className:'secondary-button'},'←'),next=E('button',{type:'button',className:'secondary-button'},'→');
    prev.setAttribute('aria-label','Предыдущие подразделения');next.setAttribute('aria-label','Следующие подразделения');
    const entry=E('div');
    const fill=()=>{
      const rows=data.rows.filter(d=>(parent===''||(parent==='none'?d.pps_id==null:String(d.pps_id)===parent))&&d.name.toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru')));
      offset=Math.min(offset,Math.max(0,Math.floor((rows.length-1)/50)*50));
      count.textContent=`${rows.length ? offset+1 : 0}–${Math.min(offset+50,rows.length)} из ${rows.length}`;prev.disabled=offset===0;next.disabled=offset+50>=rows.length;
      body.replaceChildren(...rows.slice(offset,offset+50).map(d=>E('tr',{},
        E('td',{'data-label':'Подразделение'},d.name),E('td',{'data-label':'ППС'},d.pps_name||'Не указан'),
        E('td',{'data-label':'Тип'},d.smu_id?'СМУ':'Подразделение'),E('td',{'data-label':'Сотрудников'},String(d.employee_count)),
        E('td',{'data-label':'Доступность'},d.active?'Доступно':'Отключено'),
        E('td',{'data-label':'Действия'},d.smu_id?E('button',{type:'button',className:'text-button',onclick:()=>document.querySelector('[data-catalog="smu"]').click()},'Открыть СМУ'):
          data.editable?E('div',{className:'division-actions'},E('button',{type:'button',className:'text-button',onclick:()=>{if(!canLeave())return;dirty=false;entry.replaceChildren(editor(d));entry.scrollIntoView({block:'nearest'});}},'Изменить'),E('button',{type:'button',className:'text-button',disabled:!!d.employee_count,title:d.employee_count?'Есть привязанные сотрудники':'Удалить подразделение',onclick:()=>{if(canLeave()&&confirm('Удалить «'+d.name+'»?'))mutate('/'+d.id,'DELETE',{expected_token:d.edit_token});}},'Удалить')):'—'))));
    };
    input.addEventListener('input',()=>{search=input.value;offset=0;fill();});pps.addEventListener('change',()=>{parent=pps.value;offset=0;fill();});prev.onclick=()=>{offset-=50;fill();};next.onclick=()=>{offset+=50;fill();};
    root.replaceChildren(E('div',{className:'page-heading'},E('div',{},E('h2',{},'Подразделения'),E('p',{},'ППС → подразделение. СМУ связаны с существующим справочником: название и ППС изменяются там.'))),
      E('div',{className:'division-toolbar'},E('label',{},'Поиск',input),E('label',{},'ППС',pps),E('button',{type:'button',className:'secondary-button',onclick:()=>{if(canLeave())load(true);}},'Обновить'),...(data.editable?[E('button',{type:'button',className:'primary-button',onclick:()=>{if(!canLeave())return;dirty=false;entry.replaceChildren(editor());}},'Добавить подразделение')]:[])),status,entry,
      E('div',{className:'division-pagination'},count,prev,next),E('div',{className:'division-table-wrap'},E('table',{className:'division-table'},E('thead',{},E('tr',{},...['Подразделение','ППС','Тип','Сотрудников','Доступность','Действия'].map(v=>E('th',{},v)))),body)));
    fill();
  }
  window.addEventListener('beforeunload',event=>{if(!root.hidden&&dirty){event.preventDefault();event.returnValue='';}});
  window.divisionsScreen={load,canLeave};
})();
