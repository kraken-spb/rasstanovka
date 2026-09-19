((root) => {
  'use strict';
  const collator = new Intl.Collator('ru', {numeric:true, sensitivity:'base'});
  function compare(a,b,levels,value) {
    for(const {field,direction} of levels) {
      const x=value(a,field), y=value(b,field), emptyX=x==null||String(x).trim()==='', emptyY=y==null||String(y).trim()==='';
      if(emptyX || emptyY) {if(emptyX!==emptyY)return emptyX?1:-1;continue;}
      const result=collator.compare(String(x),String(y));
      if(result)return direction==='desc'?-result:result;
    }
    return collator.compare(String(value(a,'name')||''),String(value(b,'name')||'')) || a.id-b.id;
  }
  function mount({key,fields,canApply=()=>true,apply}) {
    const node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
    const button=node('button');button.type='button';button.className='secondary-button column-menu-toggle table-sort-toggle';
    button.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 5h10M4 10h7M4 15h4M18 4v16m-3-3 3 3 3-3"/></svg>';
    button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-expanded','false');
    const scope=()=>typeof key==='function'?key():key;
    const choices=()=>typeof fields==='function'?fields():fields;
    const get=()=>{
      const saved=root.staffingPreferences?.get(scope(),[])||[], available=choices(), seen=new Set();
      return (Array.isArray(saved)?saved:[]).filter(s=>s&&Object.hasOwn(available,s.field)&&['asc','desc'].includes(s.direction)&&!seen.has(s.field)&&seen.add(s.field)).slice(0,8);
    };
    let popup=null, cleanupPopup=null;
    function refresh() {
      const levels=get();button.classList.toggle('has-sort-levels',levels.length>0);
      button.title='Сортировка: '+(levels.length?levels.map((s,i)=>`${i+1}. ${choices()[s.field]} ${s.direction==='desc'?'↓':'↑'}`).join(' → '):'ФИО ↑');
      button.setAttribute('aria-label','Настроить сортировку');
    }
    function close() {if(popup){const old=popup;popup=null;old.hidePopover();old.remove();}cleanupPopup?.();cleanupPopup=null;button.setAttribute('aria-expanded','false');}
    button.addEventListener('click',()=>{
      if(popup){close();return;}
      if(!canApply())return;
      const openedScope=scope(), available=choices();let draft=structuredClone(get()), position=()=>{};
      popup=node('div');popup.className='table-sort-popup column-menu-popup';popup.setAttribute('popover','auto');popup.setAttribute('role','dialog');popup.setAttribute('aria-label','Многоуровневая сортировка');
      const heading=node('strong','Сортировка по уровням'), hint=node('p','Первый уровень — основной, следующие сортируют внутри него. Пустые значения — внизу.'), list=node('ol');
      const actions=node('div');actions.className='table-sort-actions';
      const makeButton=(text,label,action)=>{const b=node('button',text);b.type='button';b.className='secondary-button';b.setAttribute('aria-label',label);b.onclick=action;return b;};
      const add=makeButton('+ Уровень','Добавить уровень сортировки',()=>{const field=Object.keys(available).find(k=>!draft.some(s=>s.field===k));if(field){draft.push({field,direction:'asc'});paint();}});
      function paint() {
        list.replaceChildren(...draft.map((sort,index)=>{
          const li=node('li'), row=node('div'), order=node('div');row.className='table-sort-field';order.className='table-sort-order';
          const select=node('select');select.setAttribute('aria-label','Столбец уровня '+(index+1));
          for(const [field,label] of Object.entries(available)) {const o=node('option',label);o.value=field;o.disabled=draft.some((s,i)=>i!==index&&s.field===field);select.append(o);}
          select.value=sort.field;select.onchange=()=>{sort.field=select.value;paint();};
          const direction=node('select');direction.setAttribute('aria-label','Направление уровня '+(index+1));
          for(const [value,label] of [['asc','↑ По возрастанию'],['desc','↓ По убыванию']]){const o=node('option',label);o.value=value;direction.append(o);}
          direction.value=sort.direction;direction.onchange=()=>sort.direction=direction.value;
          const up=makeButton('↑','Поднять уровень '+(index+1),()=>{[draft[index-1],draft[index]]=[draft[index],draft[index-1]];paint();});up.disabled=index===0;
          const down=makeButton('↓','Опустить уровень '+(index+1),()=>{[draft[index+1],draft[index]]=[draft[index],draft[index+1]];paint();});down.disabled=index===draft.length-1;
          const remove=makeButton('×','Удалить уровень '+(index+1),()=>{draft.splice(index,1);paint();});
          row.append(select,direction);order.append(up,down,remove);li.append(row,order);return li;
        }));
        if(!draft.length)list.append(node('p','По умолчанию: ФИО ↑.'));
        add.disabled=draft.length>=Math.min(8,Object.keys(available).length);
        position();
      }
      const reset=makeButton('Сбросить','Сбросить сортировку',()=>{draft=[];paint();});
      const cancel=makeButton('Отмена','Отменить сортировку',()=>{close();button.focus();});
      const save=makeButton('Применить','Применить сортировку',async()=>{
        if(scope()!==openedScope || !canApply())return;
        root.staffingPreferences.set({[openedScope]:draft});close();refresh();await apply();button.focus();
      });save.className='primary-button';
      actions.append(reset,cancel,save);popup.append(heading,hint,list,add,actions);document.body.append(popup);paint();
      position=()=>{if(!popup)return;const rect=button.getBoundingClientRect(),width=Math.min(520,innerWidth-24);popup.style.width=width+'px';popup.style.maxHeight=Math.max(120,innerHeight-24)+'px';popup.style.left=Math.max(12,Math.min(rect.right-width,innerWidth-width-12))+'px';popup.style.top=Math.max(12,Math.min(rect.top-popup.offsetHeight-6,innerHeight-popup.offsetHeight-12))+'px';};
      const current=popup;
      popup.addEventListener('toggle',e=>{if(e.newState==='closed'&&popup===current)close();});
      cleanupPopup=()=>window.removeEventListener('resize',position);
      popup.showPopover();position();button.setAttribute('aria-expanded','true');window.addEventListener('resize',position);
      popup.querySelector('select,button')?.focus();
    });
    window.addEventListener('hashchange',()=>{close();refresh();});
    refresh();return {button,get,refresh,close};
  }
  const api={compare,mount};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.TableSort=api;
})(globalThis);
