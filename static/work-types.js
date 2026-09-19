(() => {
  'use strict';
  const panel=document.getElementById('view-work-types');if(!panel)return;
  const root=document.querySelector('.app-shell'),editable=['admin','super_admin'].includes(root.dataset.role);
  let rows=[],busy=false,dirty=false,search='';
  const el=(tag,props={},...children)=>{const n=document.createElement(tag);for(const [k,v]of Object.entries(props)){if(k.startsWith('on'))n.addEventListener(k.slice(2),v);else if(k in n)n[k]=v;else n.setAttribute(k,v);}children.flat().forEach(c=>{if(c!=null)n.append(c);});return n;};
  const message=el('p',{role:'status'}),list=el('div');
  const button=(label,fn)=>el('button',{type:'button',className:'secondary-button',onclick:fn},label);
  async function api(path='',options={}){const response=await fetch('/api/work-types'+path,{...options,cache:'no-store',headers:{'Content-Type':'application/json','X-CSRF-Token':root.dataset.csrf}});const data=await response.json();if(!response.ok)throw Error(data.error||'Не удалось сохранить вид работ.');return data;}
  const name=el('input',{required:true,maxLength:200,'aria-label':'Новый вид работ'});
  const form=el('form',{className:'wf-toolbar',hidden:!editable},el('label',{},'Новый вид работ',name),el('button',{className:'primary-button'},'Добавить'));
  name.addEventListener('input',()=>dirty=!!name.value);
  form.onsubmit=async e=>{e.preventDefault();if(busy)return;busy=true;panel.inert=true;try{await api('',{method:'POST',body:JSON.stringify({name:name.value})});name.value='';dirty=false;await load();message.textContent='Вид работ добавлен.';}catch(e){message.textContent=e.message;}finally{busy=false;panel.inert=false;}};
  const query=el('input',{type:'search',placeholder:'Поиск по названию','aria-label':'Поиск видов работ',oninput:e=>{search=e.target.value;render();}});
  panel.append(el('div',{className:'page-heading'},el('h2',{},'Виды работ'),button('Обновить',()=>load().catch(e=>message.textContent=e.message))),form,
    el('div',{className:'wf-toolbar'},el('label',{},'Поиск',query)),message,list);
  function editor(row,tr){
    if(busy||dirty){message.textContent='Сначала сохраните текущие изменения.';return;}
    dirty=true;query.disabled=true;form.inert=true;const input=el('input',{value:row.name,maxLength:200,required:true,'aria-label':'Название вида работ'});
    const active=el('input',{type:'checkbox',checked:!!row.active,'aria-label':'Доступен для назначения'});
    tr.replaceChildren(el('td',{'data-label':'Вид работ'},input),el('td',{'data-label':'Доступен'},active),el('td',{'data-label':'Действия'},
      button('Сохранить',async()=>{if(!input.reportValidity()||busy)return;busy=true;panel.inert=true;try{await api('/'+row.id,{method:'PATCH',body:JSON.stringify({name:input.value,active:active.checked,expected_token:row.edit_token})});dirty=false;await load();message.textContent='Вид работ сохранён.';}catch(e){message.textContent=e.message;}finally{busy=false;panel.inert=false;}}),
      button('Отмена',()=>{dirty=false;render();})));
  }
  function render(){
    if(!dirty){query.disabled=false;form.inert=false;}
    const shown=rows.filter(r=>r.name.toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru')));
    const body=el('tbody');for(const row of shown){const tr=el('tr');tr.append(el('td',{'data-label':'Вид работ'},row.name),el('td',{'data-label':'Статус'},row.active?'Доступен':'Отключён'),el('td',{'data-label':'Действия'},editable?button('Изменить',()=>editor(row,tr)):'—'));body.append(tr);}
    list.replaceChildren(el('p',{},'Показано: '+shown.length+' из '+rows.length),el('table',{className:'catalog-data-table'},el('thead',{},el('tr',{},...['Вид работ','Статус','Действия'].map(v=>el('th',{scope:'col'},v)))),body));
  }
  async function load(){if(dirty){message.textContent='Сначала сохраните изменения или нажмите «Отмена».';return;}rows=(await api()).rows;render();}
  window.workTypesScreen={load,canLeave:()=>!busy&&(!dirty||panel.hidden)};
})();
