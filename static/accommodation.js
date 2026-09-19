(() => {
  'use strict';
  window.createAccommodationRequest = ({api, E}) => {
    let dialog, saving;
    const text = value => value == null ? '' : String(value);
    function input(type, value = '', required = false, aria = '') { return E('input', {type, value:text(value), required, 'aria-label':aria}); }
    async function open(ids, date) {
      if (dialog?.open || saving || !ids.length) return;
      const trigger = document.activeElement;
      const box = E('dialog', {className:'accommodation-dialog', 'aria-labelledby':'accommodation-title'});
      dialog = box;
      const message = E('p', {className:'accommodation-message', role:'status'}, 'Подготавливаем выбранных сотрудников…');
      const close = E('button', {type:'button', className:'secondary-button', onclick:() => box.close()}, 'Отмена');
      box.append(E('h2', {id:'accommodation-title'}, 'Заявка на заселение сотрудника АО "Ленгазспецстрой" в ПВП'), message, close);
      box.addEventListener('cancel', event => {if (saving) event.preventDefault();});
      box.addEventListener('close', () => {box.remove(); if (dialog === box) dialog = null; trigger?.focus({preventScroll:true});});
      document.body.append(box); box.showModal();
      try {
        const data = await api('accommodation/prepare', {method:'POST', body:JSON.stringify({ids, date})});
        if (!box.open) return;
        const all = E('input', {type:'checkbox'}), blanks = E('input', {type:'checkbox'});
        const common = {
          object: input('text','','','Объект строительства — общее значение'), worker_category:E('select', {'aria-label':'Категория — общее значение'}, E('option',{value:''},'Не указано'),E('option',{value:'ИТР'},'ИТР'),E('option',{value:'Рабочий'},'Рабочий')),
          arrival_date:input('date','','','Дата заезда — общее значение'), arrival_time:input('time','','','Время заезда — общее значение'), departure_date:input('date','','','Ориентировочный выезд — общее значение'), responsible:input('text', data.responsible_default,false,'Ответственный УРП — общее значение'), basis:input('text','','','Основание — общее значение')};
        const labels={object:'Объект строительства',worker_category:'Категория',arrival_date:'Дата заезда',arrival_time:'Время заезда',departure_date:'Ориентировочный выезд',responsible:'Ответственный УРП',basis:'Основание (необязательно)'};
        const apply = E('button',{type:'button',className:'secondary-button'},'Применить общие значения');
        const commonBox=E('fieldset',{className:'accommodation-common'},E('legend',{},'Общие значения'),E('label',{className:'check-label'},all,'Заполнить все строки, включая уже заполненные'),E('label',{className:'check-label'},blanks,'Применить пустые общие значения'),...Object.entries(common).map(([key,node])=>E('label',{},labels[key],node)),apply);
        const rows = E('div',{className:'accommodation-rows'});
        const rowInputs=[];
        for (const row of data.rows || []) {
          const values={object:row.object,worker_category:row.worker_category,arrival_date:row.arrival_date,arrival_time:row.arrival_time,departure_date:row.departure_date,responsible:row.responsible || data.responsible_default,basis:row.basis};
          const person=text(row.full_name || row.personnel_no || row.id);
          const fields={object:input('text',values.object,false,'Объект строительства: '+person),worker_category:E('select',{required:false,'aria-label':'Категория: '+person},E('option',{value:''},'Выберите'),E('option',{value:'ИТР'},'ИТР'),E('option',{value:'Рабочий'},'Рабочий')),arrival_date:input('date',values.arrival_date,false,'Дата заезда: '+person),arrival_time:input('time',values.arrival_time,false,'Время заезда: '+person),departure_date:input('date',values.departure_date,false,'Ориентировочный выезд: '+person),responsible:input('text',values.responsible,false,'Ответственный УРП: '+person),basis:input('text',values.basis,false,'Основание: '+person)};
          fields.worker_category.value=text(values.worker_category);
          const missing=['birth_date','citizenship','phone'].filter(key=>!row[key]).map(key=>({birth_date:'дата рождения',citizenship:'гражданство',phone:'телефон'})[key]);
          rows.append(E('article',{className:'accommodation-row'},E('h3',{},[row.personnel_no,row.full_name].filter(Boolean).join(' · ')),E('p',{className:'accommodation-facts'},[row.profession,row.category,row.birth_date,row.citizenship,row.phone,row.accommodation].filter(Boolean).join(' · ') || '—'),missing.length ? E('p',{className:'accommodation-missing',role:'status'},'Не заполнено: '+missing.join(', ')+'; в Excel останется пустым.') : null,...Object.entries(fields).map(([key,node])=>E('label',{},labels[key],node))));
          rowInputs.push({row,fields});
        }
        apply.addEventListener('click',()=>rowInputs.forEach(({fields})=>Object.entries(common).forEach(([key,node])=>{if((node.value || blanks.checked) && (all.checked || !fields[key].value)) fields[key].value=node.value;})));
        const submit=E('button',{type:'submit',className:'primary-button'},'Скачать заявку');
        const form=E('form',{className:'accommodation-form'},commonBox,rows,E('div',{className:'accommodation-actions'},close,submit));
        box.replaceChildren(E('h2',{id:'accommodation-title'},'Заявка на заселение сотрудника АО "Ленгазспецстрой" в ПВП'),E('p',{},`Выбрано: ${rowInputs.length}. Можно скачать без полного заполнения. Неуказанные данные останутся пустыми.`),form,message);
        message.textContent=`Готово: выбрано сотрудников — ${rowInputs.length}.`;
        let attempt;
        form.addEventListener('submit',async event=>{event.preventDefault(); if(saving || !form.reportValidity()) return;
          const payload={date:data.date || date,rows:rowInputs.map(({row,fields})=>({id:row.id,token:row.token,...Object.fromEntries(Object.entries(fields).map(([key,node])=>[key,node.value]))}))};
          const signature=JSON.stringify(payload); if(!attempt || attempt.signature!==signature) attempt={signature,key:crypto.randomUUID()};
          saving=true;form.inert=true;message.textContent='Формируем Excel…';
          try { const response=await fetch('/api/workforce/accommodation/export',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('.app-shell').dataset.csrf},body:JSON.stringify({...payload,request_key:attempt.key})});
            if(!response.ok) await window.readApiResponse(response,'Не удалось сформировать заявку.');
            if(!response.headers.get('Content-Type')?.includes('spreadsheetml.sheet')) throw new Error('Сервер вернул не Excel-файл. Повторите запрос после обновления страницы.');
            const url=URL.createObjectURL(await response.blob()), link=E('a',{href:url,download:'Заявка_ПВП_'+payload.date+'.xlsx'});document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);message.textContent=`Файл готов. Сотрудников: ${response.headers.get('X-Export-Row-Count') || rowInputs.length}.`;
          } catch(error){message.textContent=error.message;} finally {saving=false;form.inert=false;}
        });
      } catch(error) {message.textContent=error.message;}
    }
    return {open, busy:()=>!!saving};
  };
})();
