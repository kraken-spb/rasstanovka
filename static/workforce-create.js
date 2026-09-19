(() => {
  'use strict';
  window.createWorkforcePerson = ({api, E, onCreated}) => {
    const panel = document.querySelector('#wf-create-panel'), opener = document.querySelector('#wf-create-open');
    let dirty = false, busy = false, options = null, form = null, requestKey = null;
    const canLeave = () => !busy && (!dirty || window.confirm('В форме есть несохранённые данные. Закрыть её?'));
    function close() {
      panel.hidden = true; panel.replaceChildren(); opener.setAttribute('aria-expanded', 'false');
      dirty = false; form = null;
    }
    async function open() {
      if (!panel.hidden) {panel.querySelector('input')?.focus(); return;}
      panel.hidden = false;opener.setAttribute('aria-expanded','true');
      panel.textContent = 'Загрузка формы…'; busy = true;
      try {options = await api('people/create-options');render();}
      catch (error) {
        panel.replaceChildren(E('p',{role:'alert',className:'error-text'},error.message),
          E('button',{type:'button',onclick:()=>{close();opener.focus();}},'Закрыть'));
      } finally {busy=false;}
    }
    function render() {
      requestKey = crypto.randomUUID();dirty = false;
      const error = E('p',{role:'alert',className:'error-text',hidden:true});
      const controls = {}, grid = E('div',{className:'wf-create-grid'});
      form = E('form', {oninput:()=>{dirty=true;},onchange:()=>{dirty=true;}});
      function field(key, label, type='text', required=false, limit=300) {
        const input = E('input',{id:'wf-create-'+key,name:key,type,required,maxLength:limit,autocomplete:'off'});
        controls[key]=input;grid.append(E('label',{htmlFor:input.id},label + (required ? ' *' : ''),input));return input;
      }
      field('full_name','ФИО','text',true,200);
      const number=field('personnel_no','Табельный номер','text',false,100);
      number.placeholder='При наличии';
      for (const key of ['smu_id','employer_id','contractor_id','profession_code','category_id','employment_code','citizenship_code']) {
        const reference=options.fields[key], input=E('select',{id:'wf-create-'+key,name:key,required:['smu_id','employment_code'].includes(key)},
          E('option',{value:''},'Не указано'),reference.options.map(row=>E('option',{value:row.value},row.label)));
        controls[key]=input;
        const label=E('label',{htmlFor:input.id},reference.label+(input.required?' *':''));
        if (reference.options.length>20) {
          const search=E('input',{type:'search',placeholder:'Поиск по справочнику','aria-label':'Поиск: '+reference.label,autocomplete:'off'});
          search.addEventListener('input',()=>{
            const query=search.value.toLocaleLowerCase('ru').replace(/ё/g,'е');
            for(const option of input.options) option.hidden=!!option.value && !option.selected && !option.textContent.toLocaleLowerCase('ru').replace(/ё/g,'е').includes(query);
          });label.append(search);
        }
        label.append(input);grid.append(label);
      }
      if(options.fields.employment_code.options.some(row=>row.value==='employment.recruitment'))controls.employment_code.value='employment.recruitment';
      const contractor=options.fields.contractor_id.options.find(row=>row.label.toLocaleUpperCase('ru')==='ЛГСС');
      if(contractor)controls.contractor_id.value=contractor.value;
      number.addEventListener('input',()=>{
        if(options.can_create_staff && /^\d+$/.test(number.value.trim()))controls.employment_code.value='employment.staff';
      });
      field('birth_date','Дата рождения','date');field('phone','Телефон','tel');field('email','E-mail','email',false,254);field('notes','Примечание','text',false,10000);
      const refresh=E('button',{type:'button',className:'secondary-button',onclick:async()=>{
        if(busy)return;busy=true;form.inert=true;
        try {
          options=await api('people/create-options');
          for(const [key,ref] of Object.entries(options.fields)) {
            const input=controls[key], previous=input.value;
            input.replaceChildren(E('option',{value:''},'Не указано'),ref.options.map(row=>E('option',{value:row.value},row.label)));
            input.value=previous;
          }
          error.hidden=true;
        }catch(err){error.textContent=err.message;error.hidden=false;}
        finally{busy=false;form.inert=false;}
      }},'Обновить справочники');
      form.addEventListener('submit',async event=>{
        event.preventDefault();if(busy || !form.reportValidity())return;
        const data=Object.fromEntries(Object.entries(controls).map(([key,input])=>[key,input.value.trim()]));
        data.tokens=Object.fromEntries(Object.entries(options.fields).map(([key,ref])=>[key,ref.token]));data.request_key=requestKey;
        busy=true;form.inert=true;error.hidden=true;
        try {
          const person=await api('people',{method:'POST',body:JSON.stringify(data)});
          busy=false;close();await onCreated(person);
        }catch(err){error.textContent=err.message;error.hidden=false;}
        finally{busy=false;if(form)form.inert=false;}
      });
      form.append(grid,error,E('div',{className:'wf-create-actions'},
        E('button',{type:'submit',className:'primary-button'},'Добавить сотрудника'),
        E('button',{type:'button',className:'secondary-button',onclick:()=>{if(canLeave()){close();opener.focus();}}},'Отмена'),refresh));
      panel.replaceChildren(E('h2',{},'Новый сотрудник'),
        E('p',{className:'table-note'},'Без табельного номера будет создан внутренний код. Фактический статус (заезд, ПВП, явка) укажите после добавления.'),form);
      controls.full_name.focus();
    }
    window.addEventListener('beforeunload',event=>{if(dirty||busy){event.preventDefault();event.returnValue='';}});
    return {open,close,canLeave};
  };
})();
