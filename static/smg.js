(() => {
  'use strict';
  const objectKey = (scope, object) => scope+'\n'+object;
  function objectsFor(edition) {
    const objects=new Map();
    for(const month of edition.months)for(const obj of month.breakdown?.objects || [])
      objects.set(objectKey(edition.scope,obj.scope),{key:objectKey(edition.scope,obj.scope),label:obj.scope+' · '+obj.label});
    return objects.size ? [...objects.values()] : [{key:objectKey(edition.scope,'whole'),label:edition.scope+' · Общий план'}];
  }
  function filteredMonths(editions, scopes, objects, periods, kind, nodes) {
    const selected=editions.filter(e=>scopes.has(e.scope)).map(e=>({edition:e,objects:objectsFor(e).filter(o=>objects.has(o.key))})).filter(e=>e.objects.length);
    return periods.map(period=>{
      const parts=selected.flatMap(({edition,objects:chosen})=>{
        const month=edition.months.find(m=>m.period===period);
        if(!month)return [null];
        if(!month.breakdown)return kind==='staff' && chosen.length===objectsFor(edition).length ? [month.values] : [null];
        return chosen.map(obj=>month.breakdown.objects.find(o=>objectKey(edition.scope,o.scope)===obj.key)?.values[kind] || null);
      });
      return {period,values:Object.fromEntries(nodes.map(n=>{
        const values=parts.map(p=>p?.[n.id]);
        return [n.id,!values.length||values.some(v=>v==null)?null:values.reduce((a,v)=>a+Number(v),0)];
      }))};
    });
  }
  if(typeof module!=='undefined' && module.exports){module.exports={objectsFor,filteredMonths};return;}
  const $ = id => document.getElementById(id);
  if (!$('view-smg')) return;
  const root = document.querySelector('.app-shell');
  const state = {busy:false, preview:null, token:null, nodes:[], requestKey:null, planNodes:[], editions:[], versions:[], scopes:new Set(), objects:new Set(), periods:new Set(), kind:'staff', initialized:false, history:null};
  const E = (tag, text, props={}) => {const n=document.createElement(tag);n.textContent=text;Object.assign(n,props);return n;};
  const error = (message='', importing=false) => {const n=$(importing?'smg-import-error':'smg-error');n.textContent=message;n.hidden=!message;};
  const display = value => value == null ? '—' : Number(value).toLocaleString('ru-RU',{maximumFractionDigits:8});
  const equal = (a,b) => a == null || b == null ? a == null && b == null : Number(a) === Number(b);
  const period = value => new Date(value+'T00:00:00').toLocaleDateString('ru-RU',{month:'long',year:'numeric'});
  async function api(path, options={}) {
    const r=await fetch('/api/smg/'+path,{...options,headers:{'X-CSRF-Token':root.dataset.csrf,...options.headers}});
    const data=await r.json();if(!r.ok)throw new Error(data.error||'Не удалось выполнить запрос.');return data;
  }
  function table(target, nodes, months, reviewing=false) {
    const t=E('table','',{className:'smg-table'}), head=E('thead',''), row=E('tr','');
    row.append(E('th','ГДЛР'));
    for(const month of months) {
      const th=E('th','');
      th.append(E('span',period(month.period),{className:'smg-month-full'}),
        E('span',month.period.slice(5,7)+'.'+month.period.slice(0,4),{className:'smg-month-short',title:period(month.period)}));
      row.append(th);
    }
    head.append(row);t.append(head);const body=E('tbody',''), depths=new Map();
    for(const n of nodes) {
      const depth=n.parent_id?(depths.get(n.parent_id)||0)+1:0;depths.set(n.id,depth);
      const tr=E('tr','',{className:n.is_group?'smg-group':''}), name=E('td',''), label=E('div',n.category_name||n.label,{className:'smg-label'});
      label.style.setProperty('--depth',depth);name.append(label);tr.append(name);
      for(const month of months) {
        const value=month.values[n.id], previous=month.previous?.[n.id];
        const changed=reviewing && !equal(previous,value);
        const td=E('td',display(value),{className:changed?'smg-changed':'',title:value??'План не задан'});
        if(changed)td.append(E('small','Было: '+display(previous),{className:'smg-before'}));
        tr.append(td);
      }
      body.append(tr);
    }
    t.append(body);target.replaceChildren(t);
  }
  let loadGeneration=0;
  const normalized = value => value.toLocaleLowerCase('ru-RU').replace(/ё/g,'е').trim();
  const matching = label => normalized(label).includes(normalized($('smg-search').value));
  const scopeEditions = () => state.editions.filter(e=>state.scopes.has(e.scope));
  const availableObjects = () => scopeEditions().flatMap(objectsFor);
  const allPeriods = () => [...new Set(state.editions.flatMap(e=>e.months.map(m=>m.period)))].sort();
  const periodFilter=window.DateFilter.mount({title:'Период планов СМГ',allowEmpty:false,onChange(value){
    state.periods=new Set(allPeriods().filter(p=>value.values.length?value.values.some(day=>day.slice(0,7)===p.slice(0,7)):
      (!value.from||window.DateFilter.bounds('month',p.slice(0,7)).to>=value.from)&&(!value.to||p<=value.to)));
    showPlan();
  }});
  $('smg-periods').append(periodFilter.button);
  function chips(id, options, selected, change, radio=false) {
    const container=$(id);container.replaceChildren();
    function chip(value,label,checked,handler,all=false) {
      const item=E('label','',{className:'smg-chip',title:label}),input=E('input','',{type:radio?'radio':'checkbox',checked});
      input.value=value;input.name=id;
      if(all)input.indeterminate=options.some(o=>selected.has(o.key))&&!checked;
      input.addEventListener('change',()=>handler(input.checked));
      item.append(input,E('span',label));container.append(item);
    }
    if(!radio&&options.length)chip('*','Все',options.every(o=>selected.has(o.key)),checked=>change(options.map(o=>o.key),checked),true);
    for(const option of options)chip(option.key,option.label,selected.has(option.key),checked=>change([option.key],checked));
    if(!options.length)container.append(E('span','Нет подходящих значений',{className:'smg-filter-empty'}));
  }
  function toggle(set,keys,checked){for(const key of keys)checked?set.add(key):set.delete(key);}
  function renderFilters() {
    const focused=document.activeElement,focusName=focused?.name,focusValue=focused?.value;
    const options=state.editions.map(e=>({key:e.scope,label:e.scope})).filter(o=>matching(o.label)||objectsFor(state.editions.find(e=>e.scope===o.key)).some(x=>matching(x.label)));
    chips('smg-scopes',options,state.scopes,(keys,checked)=>{
      toggle(state.scopes,keys,checked);
      if(checked)for(const e of state.editions.filter(e=>keys.includes(e.scope)))for(const o of objectsFor(e))state.objects.add(o.key);
      renderFilters();showPlan();
    });
    chips('smg-objects',availableObjects().filter(o=>matching(o.label)),state.objects,(keys,checked)=>{toggle(state.objects,keys,checked);renderFilters();showPlan();});
    periodFilter.setOptions(allPeriods());
    chips('smg-kinds',[{key:'staff',label:'СС'},{key:'rental',label:'Аренда'},{key:'outstaff',label:'Аутстафф'},{key:'total',label:'Итого'}],new Set([state.kind]),keys=>{state.kind=keys[0];renderFilters();showPlan();},true);
    if(focusName)Array.from($('smg-filters').querySelectorAll('input')).find(input=>input.name===focusName&&input.value===focusValue)?.focus({preventScroll:true});
  }
  function resetFilters() {
    state.scopes=new Set(state.editions.map(e=>e.scope));state.objects=new Set(state.editions.flatMap(objectsFor).map(o=>o.key));
    const latest=state.versions[0];state.periods=new Set(latest?.source.periods || (latest?[latest.period]:allPeriods()));
    if(state.history)state.periods=new Set(allPeriods());
    const periods=[...state.periods].sort();periodFilter.set(periods.length?{from:periods[0],to:window.DateFilter.bounds('month',periods.at(-1).slice(0,7)).to}:{});
    state.kind='staff';$('smg-search').value='';$('smg-category-search').value='';renderFilters();showPlan();
  }
  function showPlan() {
    const periods=allPeriods().filter(p=>state.periods.has(p)),objects=availableObjects().filter(o=>state.objects.has(o.key));
    const months=filteredMonths(state.editions,state.scopes,state.objects,periods,state.kind,state.planNodes);
    const query=normalized($('smg-category-search').value),keep=new Set(),parents=new Map(state.planNodes.map(n=>[n.id,n.parent_id]));
    if(query)for(const n of state.planNodes)if(normalized(n.category_name||n.label).includes(query)){
      let id=n.id;while(id&&!keep.has(id)){keep.add(id);id=parents.get(id);}
    }
    const nodes=query?state.planNodes.filter(n=>keep.has(n.id)):state.planNodes;
    $('smg-source').textContent=`${state.history?'Редакция из истории · ':''}ППС: ${state.scopes.size} · Объекты: ${objects.length} · Месяцы: ${periods.length}. Сумма выбранного; «—» — неполные данные.${query?' Поиск ГДЛР не меняет итоги.':''}`;
    if(!objects.length||!periods.length||!nodes.length){$('smg-table').replaceChildren(E('p',!nodes.length?'Категории не найдены.':'Отметьте ППС, объекты и месяцы для просмотра.',{className:'table-note'}));return;}
    table($('smg-table'),nodes,months);
  }
  $('smg-search').addEventListener('input',renderFilters);$('smg-category-search').addEventListener('input',showPlan);
  $('smg-reset').addEventListener('click',resetFilters);$('smg-latest').addEventListener('click',()=>{state.initialized=false;load();});
  async function load(selected=null) {
    const generation=++loadGeneration;
    error();state.busy=true;
    try {
      const data=await api('plans'+(selected?'?version='+encodeURIComponent(selected):'?latest=1'));
      if(generation!==loadGeneration)return;
      const wasHistory=state.history;
      state.history=selected;state.versions=data.versions;state.planNodes=data.hierarchy;
      state.editions=(selected?[{scope:data.scope,months:data.months}]:data.editions).sort((a,b)=>a.scope.localeCompare(b.scope,'ru',{numeric:true}));
      $('smg-editions').replaceChildren(...data.versions.map(v=>{
        const b=E('button',`${v.scope} · ${period(v.period)} · ${new Date(v.created_at).toLocaleString('ru-RU')}`,{type:'button',className:'smg-chip',title:`${v.source.filename} · ${v.author}`});
        b.setAttribute('aria-pressed',String(v.id===selected));b.addEventListener('click',()=>load(v.id));return b;
      }));
      $('smg-latest').hidden=!selected;
      if(!state.initialized||selected||wasHistory){state.initialized=true;resetFilters();}
      else{
        state.scopes=new Set([...state.scopes].filter(s=>state.editions.some(e=>e.scope===s)));
        renderFilters();showPlan();
      }
    } catch(e){if(generation===loadGeneration)error(e.message);}finally{if(generation===loadGeneration)state.busy=false;}
  }
  function selectedBlock(){return state.preview?.blocks[Number($('smg-block').value)];}
  function previewBlock() {
    const block=selectedBlock();if(!block)return;
    state.requestKey=crypto.randomUUID();$('smg-ack').checked=false;
    const notes=[...block.errors,...block.months.flatMap(m=>[...m.errors,...m.warnings].map(s=>period(m.period)+': '+s))];
    if(block.duplicate)notes.unshift('В книге несколько общих блоков этого ППС. Проверьте выбранную строку.');
    $('smg-warnings').replaceChildren(...notes.map(t=>E('li',t)));
    $('smg-ack-label').hidden=!notes.length;
    const changed=block.months.reduce((total,m)=>total+state.nodes.filter(n=>!equal(m.previous[n.id],m.values[n.id])).length,0);
    $('smg-replacement').textContent=`${period(block.months[0].period)} — ${period(block.months.at(-1).period)}. Все три месяца сохраняются одной редакцией; прежние редакции остаются в истории. Изменений в ячейках: ${changed}.`;
    table($('smg-preview-table'),state.nodes,block.months,true);
    updateApply();
  }
  function updateApply(){const b=selectedBlock();$('smg-apply').disabled=state.busy||!b||b.errors.length>0||b.months.some(m=>m.errors.length)||((b.months.some(m=>m.warnings.length)||b.duplicate)&&!$('smg-ack').checked);}
  $('smg-upload')?.addEventListener('click',()=>{if(state.busy)return;$('smg-import').showModal();$('smg-year').value=new Date().getFullYear();});
  $('smg-close').addEventListener('click',()=>{if(!state.busy)$('smg-import').close();});
  $('smg-import').addEventListener('cancel',e=>{if(state.busy)e.preventDefault();});
  $('smg-file-form').addEventListener('submit',async e=>{
    e.preventDefault();if(state.busy)return;error('',true);state.busy=true;$('smg-file-form').inert=true;$('smg-preview').hidden=true;
    try {
      const form=new FormData();form.append('file',$('smg-file').files[0]);form.append('year',$('smg-year').value);
      const result=await api('preview',{method:'POST',body:form});state.preview=result.document;state.token=result.token;state.nodes=result.hierarchy;
      $('smg-block').replaceChildren(...result.document.blocks.map((b,i)=>E('option',`${b.scope} · общий план · строка ${b.row}${b.duplicate?' · повтор ППС':''}`,{value:i})));
      $('smg-preview').hidden=false;previewBlock();
    }catch(e){error(e.message,true);}finally{state.busy=false;$('smg-file-form').inert=false;updateApply();}
  });
  $('smg-block').addEventListener('change',previewBlock);$('smg-ack').addEventListener('change',updateApply);
  for(const id of ['smg-file','smg-year'])$(id).addEventListener('change',()=>{state.preview=null;state.token=null;$('smg-preview').hidden=true;});
  $('smg-apply').addEventListener('click',async()=>{
    if(state.busy)return;state.busy=true;updateApply();$('smg-preview').inert=true;error('',true);
    try {
      const data=await api('apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:state.token,block:Number($('smg-block').value),acknowledge:$('smg-ack').checked,request_key:state.requestKey})});
      $('smg-import').close();state.preview=null;state.token=null;$('smg-preview').hidden=true;await load(data.id);
    }catch(e){error(e.message,true);}finally{state.busy=false;$('smg-preview').inert=false;updateApply();}
  });
  $('smg-refresh').addEventListener('click',()=>load(state.history));
  window.smgScreen={load,canLeave:()=>!state.busy&&!$('smg-import').open};
  const icon=document.querySelector('[data-view="smg"] svg path');if(icon)icon.setAttribute('d','M4 3h16v18H4z M8 7h8 M8 11h3 M8 15h3 M15 11v6 M13 15l2 2 2-2');
})();
