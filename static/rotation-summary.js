(() => {
  'use strict';
  const $ = id => document.getElementById(id), root = $('rotation-summary');
  if (!root) return;
  const MF = window.MultiFilter;
  let sequence = 0, readyQuery = null, exporting = false;
  const el = (tag, text, className) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; };
  const dateText = day => day.split('-').reverse().join('.');
  const monthEnd = (value, offset) => {
    const [y, m] = value.split('-').map(Number), d = new Date(y, m + offset, 0);
    return [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('-');
  };
  function fitTable() {
    if (!root.hidden) $('rs-table').style.maxHeight = Math.max(180, innerHeight - $('rs-table').getBoundingClientRect().top - 20) + 'px';
  }
  window.addEventListener('resize',fitTable);
  root.querySelector('details').addEventListener('toggle',fitTable);
  function dates() { if ($('rs-date').value) { $('rs-end1').value = monthEnd($('rs-date').value, 0); $('rs-end2').value = monthEnd($('rs-date').value, 1); } }
  dates();
  for (const key of ['pps', 'smu']) MF.enable($('rs-' + key));
  function params() {
    const p = new URLSearchParams({date:$('rs-date').value, end1:$('rs-end1').value, end2:$('rs-end2').value});
    for (const key of ['pps','smu']) MF.params(p, key, MF.get($('rs-' + key)));
    return p.toString();
  }
  function options(key, rows) {
    const select = $('rs-' + key), chosen = MF.get(select), wanted = MF.values(chosen);
    const all = el('option', key === 'pps' ? 'Все ППС' : 'Все СМУ'); all.value = '';
    // Retain a vanished selection explicitly: never silently broaden the requested scope.
    const list = [...rows];
    for (const value of wanted) if (!list.some(r => r.value === value)) list.push({value, label:'Недоступно в выборке'});
    select.replaceChildren(all, ...list.map(row => {const o=el('option',row.label);o.value=row.value;return o;}));
    MF.set(select, chosen);
  }
  function render(data) {
    const table = el('table', undefined, 'rotation-summary-table'), head = el('thead'), top = el('tr'), labels = el('tr');
    const category = el('th', 'Категория ГДЛР'); category.rowSpan=2;category.scope='col';top.append(category);
    let previous, span, group = -1;
    for (const col of data.columns) {
      if (col.group !== previous) {group++;span=el('th',col.group,'rs-period');span.dataset.period=String(group % 2);span.colSpan=1;span.scope='colgroup';top.append(span);previous=col.group;}
      else span.colSpan++;
      const th=el('th',col.label);th.scope='col';th.title=col.period || col.group;th.dataset.period=String(group % 2);labels.append(th);
    }
    head.append(top,labels);table.append(head);
    const body = el('tbody');
    for (const row of data.rows) {
      const tr=el('tr',undefined,row.group?'rs-group':'');
      const name=el('th',row.label);name.scope='row';name.style.setProperty('--rs-depth',Math.min(row.depth,4));tr.append(name);
      for (const col of data.columns) {
        const value=row.values[col.key], td=el('td',value == null?'—':Number(value).toLocaleString('ru-RU',{maximumFractionDigits:2}));
        if(value == null) td.title='Нет достаточных данных для расчёта';
        if(col.key.startsWith('delta') && value != null) {td.className=value<0?'rs-shortage':value>0?'rs-surplus':'';if(value>0)td.textContent='+'+td.textContent;}
        if(value != null && !/^(plan|delta|rental|dismissed|released)/.test(col.key)) {
          const scopes = ['pps','smu'].filter(key=>data.filters[key].length).map(key=>{
            const names=data.filters[key].map(value=>data.options[key].find(o=>o.value===value)?.label || 'Недоступное значение');
            return names.slice(0,2).join(', ')+(names.length>2?` и ещё ${names.length-2}`:'');
          });
          const label = [`${row.label} · ${col.label} · ${col.period || col.group}`,...scopes].join(' · ').slice(0,950);
          const selector = {date:data.date,end1:data.end1,end2:data.end2,pps:data.filters.pps,smu:data.filters.smu,node:String(row.id),metric:col.key,label};
          const route = 'workforce/rotation/summary/' + encodeURIComponent(JSON.stringify(selector));
          const link = el('a',td.textContent,'rs-drill');link.href='#'+route;
          link.title=`Открыть сотрудников: ${label}`;link.setAttribute('aria-label',`${label}: ${value}. Открыть в перевахте`);
          link.addEventListener('click',event=>{
            if(readyQuery===null || readyQuery!==params()){event.preventDefault();return;}
            if(event.button===0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey){event.preventDefault();window.openWorkforceReport(route);}
          });
          td.replaceChildren(link);
        }
        tr.append(td);
      }
      body.append(tr);
    }
    table.append(body);$('rs-table').replaceChildren(table);
    $('rs-warnings').replaceChildren(...data.warnings.map(s=>el('p',s)));
    $('rs-method').replaceChildren(...data.notes.map(s=>el('li',s)));
    $('rs-status').textContent=`На ${dateText(data.date)} · сотрудников: ${data.total.toLocaleString('ru-RU')} · категории и итоги. ${data.total ? '' : 'В выбранных ППС / СМУ нет сотрудников.'}`;
    fitTable();
  }
  async function load() {
    const id=++sequence;readyQuery=null;$('rs-export').disabled=true;$('rs-table').replaceChildren();
    if (!$('rotation-summary-filters').reportValidity()) {root.removeAttribute('aria-busy');return;}
    const query=params();
    $('rs-status').textContent='Формируем свод…';root.setAttribute('aria-busy','true');
    try {
      const response=await fetch('/api/workforce/rotation-summary?'+query,{cache:'no-store'});
      const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось загрузить свод.');
      if(id!==sequence || query!==params())return;
      for(const key of ['pps','smu'])options(key,data.options[key]);
      render(data);readyQuery=query;$('rs-export').disabled=exporting;
    } catch(error) {if(id===sequence){$('rs-status').textContent=error.message;$('rs-table').replaceChildren();$('rs-warnings').replaceChildren();}}
    finally {if(id===sequence)root.removeAttribute('aria-busy');}
  }
  $('rotation-summary-filters').addEventListener('submit',e=>{e.preventDefault();load();});
  $('rs-date').addEventListener('change',()=>{dates();load();});
  for(const key of ['end1','end2','pps','smu'])$('rs-'+key).addEventListener('change',()=>{
    if(key==='pps')MF.set($('rs-smu'),'');
    load();
  });
  $('rs-export').addEventListener('click',async()=>{
    if(exporting || readyQuery===null || readyQuery!==params())return;
    exporting=true;$('rs-export').disabled=true;
    try {
      const query=readyQuery,response=await fetch('/api/workforce/rotation-summary.xlsx?'+query,{cache:'no-store'});
      if(!response.ok){const data=await response.json();throw new Error(data.error||'Не удалось выгрузить свод.');}
      const url=URL.createObjectURL(await response.blob()),a=el('a');a.href=url;a.download='Свод перевахтовки '+new URLSearchParams(query).get('date')+'.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
    }catch(error){$('rs-status').textContent=error.message;}
    finally{exporting=false;$('rs-export').disabled=readyQuery===null||readyQuery!==params();}
  });
  window.rotationSummary={load};
})();
