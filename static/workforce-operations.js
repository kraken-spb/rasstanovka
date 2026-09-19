(() => {
  'use strict';
  window.createWorkforceOperations = ({container,api,E,displayDate,openCard}) => {
    const state={date:'',department:'',kind:'',mine:false,days:14,offset:0,group:0,mode:'attention',seq:0,data:null,reference:null};
    const kinds={departure:'Подтвердить выезд',unassigned:'Без расстановки',readiness:'Проверить готовность',movement:'Поездки',document:'Документы',conflict:'Конфликты',rotation:'Вахты'};
    const status=E('p',{role:'status','aria-live':'polite'}), body=E('div'), controls=E('div',{className:'wf-operations-toolbar'});
    const department=E('select',{'aria-label':'СМУ для рабочего дня',onchange:()=>{state.department=department.value;state.offset=0;state.group=0;refresh();}});
    const days=E('select',{'aria-label':'Горизонт прогноза',onchange:()=>{state.days=Number(days.value);refresh();}},...[7,14,30].map(n=>E('option',{value:n,selected:n===14},n+' дней')));
    const mine=E('input',{type:'checkbox',onchange:()=>{state.mine=mine.checked;state.offset=0;refresh();}});
    const tabs=E('div',{className:'wf-operations-tabs',role:'group','aria-label':'Раздел рабочего дня'});
    for(const [mode,title] of [['attention','Требует внимания'],['forecast','Прогноз состава']]) tabs.append(E('button',{type:'button',onclick:()=>{state.mode=mode;refresh();}},title));
    controls.append(E('label',{},'СМУ',department),E('label',{},'Период',days),E('label',{className:'check-label'},mine,'Взято мной'),E('button',{type:'button',onclick:refresh},'Обновить'));
    container.append(tabs,controls,status,body);
    async function refresh(){
      const seq=++state.seq;status.textContent='Загрузка…';body.setAttribute('aria-busy','true');body.inert=true;
      try {
        const data=await api('operations?'+new URLSearchParams({date:state.date,department:state.department,kind:state.kind,mine:state.mine?'1':'0',days:state.days,offset:state.offset,forecast:state.mode==='forecast'?'1':'0',group_offset:state.group,group_limit:10}));
        if(seq!==state.seq)return;
        state.data=data;state.group=data.forecast_offset;render();status.textContent='';
      } catch(error){if(seq===state.seq){state.data=null;body.replaceChildren();status.textContent=error.message;}}
      finally{if(seq===state.seq){body.removeAttribute('aria-busy');body.inert=false;}}
    }
    function editDialog(title, label, type, value, save){
      const dialog=E('dialog',{className:'wf-operation-dialog'}), field=E('input',{type,value,required:true,...(type==='number'?{min:0,max:100000,step:1}:{})});
      const error=E('p',{role:'alert'}), submit=E('button',{type:'submit',className:'primary-button'},'Сохранить');
      const form=E('form',{onsubmit:async event=>{event.preventDefault();submit.disabled=true;error.textContent='';try{await save(field.value);dialog.close();refresh();}catch(err){error.textContent=err.message;}finally{submit.disabled=false;}}},E('h2',{},title),E('label',{},label,field),error,E('div',{className:'wf-operations-page'},submit,E('button',{type:'button',onclick:()=>dialog.close()},'Отмена')));
      dialog.append(form);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();field.focus();
    }
    function confirmDeparture(item){
      const plan=item.departure;
      const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
      const maximum=state.date<today?state.date:today;
      const actual=E('input',{type:'date',required:true,min:plan.min_date,max:maximum,
        value:plan.planned_date>=plan.min_date&&plan.planned_date<=maximum?plan.planned_date:''});
      const reason=E('textarea',{maxLength:10000,rows:2,placeholder:'Необязательно для истории'});
      const error=E('p',{className:'error-text',role:'alert'});
      const submit=E('button',{type:'submit',className:'primary-button'},'Подтвердить выезд');
      const dialog=E('dialog',{className:'wf-operation-dialog wf-departure-dialog','aria-labelledby':'wf-departure-title'});
      let saving=false,attempt=null;
      const cancel=E('button',{type:'button',onclick:()=>{if(!saving)dialog.close();}},'Отмена');
      const form=E('form',{},E('h2',{id:'wf-departure-title'},'Подтвердить выезд'),E('strong',{},item.full_name),
        E('p',{},plan.source_label+' · '+displayDate(plan.planned_date)),E('label',{},'Фактическая дата выезда',actual),
        E('p',{},'С этой даты сотрудник получит статус «Неявка».'+(plan.rotation_id?' Текущая вахта будет завершена.':'')),
        E('label',{},'Основание для истории (необязательно)',reason),error,E('div',{className:'wf-operations-page'},submit,cancel));
      form.addEventListener('submit',async event=>{
        event.preventDefault();if(saving||!form.reportValidity())return;
        const values={date:state.date,worker_id:item.worker_id,key:item.key,token:plan.token,actual_date:actual.value,reason:reason.value.trim()};
        const fingerprint=JSON.stringify(values);
        if(attempt?.fingerprint!==fingerprint)attempt={fingerprint,key:crypto.randomUUID()};
        saving=true;form.inert=true;error.textContent='';submit.textContent='Сохранение…';
        try{
          await api('operations/departure',{method:'POST',body:JSON.stringify({...values,request_key:attempt.key})});
          dialog.close();state.offset=0;await refresh();status.textContent='Выезд подтверждён: '+item.full_name+' · '+displayDate(actual.value)+'. Статус — «Неявка».';
        }catch(err){error.textContent=err.message;}
        finally{saving=false;form.inert=false;submit.textContent='Подтвердить выезд';}
      });
      dialog.addEventListener('cancel',event=>{if(saving)event.preventDefault();});
      dialog.addEventListener('close',()=>dialog.remove());dialog.append(form);document.body.append(dialog);dialog.showModal();actual.focus();
    }
    function render(){
      const data=state.data;if(!data)return;
      [...tabs.children].forEach((b,i)=>{b.classList.toggle('active',(i===0)===(state.mode==='attention'));b.setAttribute('aria-pressed',String((i===0)===(state.mode==='attention')));});
      days.closest('label').hidden=state.mode!=='forecast';mine.closest('label').hidden=state.mode!=='attention';body.replaceChildren();
      if(state.mode==='forecast')return renderForecast(data);
      const counts=E('div',{className:'wf-operations-counts',role:'group','aria-label':'Тип ситуации'});
      for(const [key,title] of [['','Все'],...Object.entries(kinds)]) counts.append(E('button',{type:'button','aria-pressed':String(state.kind===key),onclick:()=>{state.kind=key;state.offset=0;refresh();}},title+(key?' · '+(data.counts[key]||0):'')));
      body.append(counts,E('details',{className:'wf-operations-method'},E('summary',{},'Как работает список'),E('p',{},'Ситуация исчезнет из списка после исправления исходных данных. Проверка замены не означает, что замена отсутствует.')));
      if(!data.rows.length)body.append(E('p',{},'По выбранным условиям ситуаций нет.'));
      for(const item of data.rows){
        const actions=E('div',{className:'wf-operations-issue-actions'},E('button',{type:'button',onclick:()=>openCard(item.worker_id)},item.kind==='departure'||item.action==='Открыть расстановку'?'Карточка':item.action));
        if(item.departure&&data.can_confirm_departure)actions.prepend(E('button',{type:'button',className:'primary-button',onclick:()=>confirmDeparture(item)},'Подтвердить выезд'));
        if(item.kind==='unassigned')actions.prepend(E('button',{type:'button',onclick:()=>window.openStaffingReport({kind:'person',date:state.date,shift:'',full_name:item.full_name})},'Расстановка'));
        if(data.can_claim&&(!item.owner_id||item.owner_id===data.actor_id||data.can_plan)) actions.append(E('button',{type:'button',onclick:()=>editDialog(item.owner_id===data.actor_id?'Изменить срок':'Взять в работу','Срок', 'date',item.due_date,value=>api('operations/claim',{method:'POST',body:JSON.stringify({date:state.date,due_date:value,key:item.key,worker_id:item.worker_id,expected_token:item.edit_token||''})}))},item.owner_id===data.actor_id?'Срок':'Взять в работу'));
        body.append(E('article',{className:'wf-operations-issue'+(item.overdue?' is-overdue':''),'data-issue-key':item.key},E('div',{},E('button',{type:'button',className:'wf-person-link',onclick:()=>openCard(item.worker_id)},item.full_name),E('small',{},item.department||'СМУ не указано'),E('p',{},item.title),item.departure?E('small',{},item.departure.source_label+' · '+displayDate(item.departure.planned_date)):null),E('div',{},E('p',{},(item.overdue?'Просрочено · ':'Срок · ')+displayDate(item.due_date)),E('small',{},item.owner_name?'Ответственный: '+item.owner_name:'Не взято в работу · '+item.team)),actions));
      }
      body.append(E('div',{className:'wf-operations-page'},E('button',{type:'button',disabled:!state.offset,onclick:()=>{state.offset=Math.max(0,state.offset-50);refresh();}},'←'),E('span',{},'Страница '+(Math.floor(state.offset/50)+1)+' из '+Math.max(1,Math.ceil(data.total/50))+' · '+data.total+' ситуаций'),E('button',{type:'button',disabled:state.offset+50>=data.total,onclick:()=>{state.offset+=50;refresh();}},'→')));
    }
    function renderForecast(data){
      body.append(E('details',{className:'wf-operations-method'},E('summary',{},'Как считается прогноз'),E('p',{},data.note)));
      if(data.can_plan)body.append(E('button',{type:'button',onclick:()=>newDemand(data)},'Задать потребность'));
      const groups=data.forecast;
      if(!groups.length)body.append(E('p',{},'Нет сотрудников для прогноза.'));
      for(const group of groups){
        const labels=['Дата','Подтверждено на '+displayDate(data.date),'Ожидается','Заезды / выезды','Потребность','Дефицит'];
        const tbody=E('tbody');
        for(const point of group.days){
          const demand=data.can_plan?E('button',{type:'button','aria-label':'Потребность на '+displayDate(point.date),onclick:()=>editDialog('Потребность · '+group.department+' · '+group.profession,'Количество сотрудников','number',point.required??'',value=>api('demand',{method:'POST',body:JSON.stringify({date:point.date,department:group.department,profession:group.profession,required:Number(value),expected_token:point.edit_token})}))},point.required===null?'Задать':String(point.required)):String(point.required??'—');
          const values=[displayDate(point.date),String(point.confirmed_base),String(point.expected),`${point.planned_arrivals} / ${point.planned_departures}`,demand,String(point.shortage??'—')];
          tbody.append(E('tr',{},...values.map((value,i)=>E('td',{'data-label':labels[i],className:i===5&&point.shortage?'wf-forecast-shortage':''},value))));
        }
        body.append(E('section',{className:'wf-forecast-group'},E('h3',{},(group.department||'Без СМУ')+' · '+group.profession),E('table',{},E('thead',{},E('tr',{},...labels.map(t=>E('th',{scope:'col'},t)))),tbody)));
      }
      body.append(E('div',{className:'wf-operations-page'},E('button',{type:'button',disabled:!state.group,onclick:()=>{state.group=Math.max(0,state.group-10);refresh();}},'←'),E('span',{},'Группы '+(groups.length?state.group+1:0)+'–'+(state.group+groups.length)+' из '+data.forecast_total),E('button',{type:'button',disabled:state.group+10>=data.forecast_total,onclick:()=>{state.group+=10;refresh();}},'→')));
    }
    function newDemand(data){
      const dialog=E('dialog',{className:'wf-operation-dialog'}), error=E('p',{role:'alert'});
      const date=E('input',{type:'date',value:state.date,required:true}), smu=E('select',{required:true},...(data.plan_departments||[]).map(name=>E('option',{value:name},name)));
      if(state.department)smu.value=state.department;
      const professions=[...new Set([...(state.reference.catalog||[]).filter(c=>c.kind==='profession'&&c.active).map(c=>c.label),...data.forecast.map(g=>g.profession)])].sort((a,b)=>a.localeCompare(b,'ru'));
      const profession=E('select',{required:true},...professions.map(name=>E('option',{value:name},name))), amount=E('input',{type:'number',min:0,max:100000,step:1,required:true});
      const submit=E('button',{type:'submit'},'Сохранить');
      dialog.append(E('form',{onsubmit:async event=>{event.preventDefault();submit.disabled=true;error.textContent='';try{
        const previous=data.forecast.find(g=>g.department===smu.value&&g.profession===profession.value)?.days.find(p=>p.date===date.value);
        await api('demand',{method:'POST',body:JSON.stringify({date:date.value,department:smu.value,profession:profession.value,required:Number(amount.value),expected_token:previous?.edit_token||''})});dialog.close();state.group=0;refresh();
      }catch(err){error.textContent=err.message;}finally{submit.disabled=false;}}},E('h2',{},'Потребность в сотрудниках'),E('label',{},'Дата',date),E('label',{},'СМУ',smu),E('label',{},'Профессия',profession),E('label',{},'Количество',amount),error,E('div',{className:'wf-operations-page'},submit,E('button',{type:'button',onclick:()=>dialog.close()},'Отмена'))));
      document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();amount.focus();
    }
    return {
      async load(day,reference){state.date=day;state.reference=reference;department.replaceChildren(E('option',{value:''},'Все доступные СМУ'),...(reference.departments||[]).map(row=>E('option',{value:row.name},row.name)));department.value=state.department;await refresh();},
      async readiness(target,id,day){
        target.textContent='Проверка готовности…';
        try{const data=await api('readiness/'+id+'?date='+encodeURIComponent(day));if(!target.isConnected)return;
          target.replaceChildren(E('h3',{},'Расстановка на '+displayDate(day)),E('p',{},data.assigned?'Есть назначение':'Назначения нет'),E('p',{},data.ready?'По проверенным данным замечаний нет':'Требует внимания'),E('ul',{},...data.reasons.map(text=>E('li',{},text))),E('small',{},data.note));
          if(data.stage_reason)target.append(E('p',{},'Основание присутствия: '+data.stage_reason));
          for(const a of data.assignments)target.append(E('p',{},a.shift+' · '+a.object_name+' / '+a.subobject_name));
        }catch(error){if(target.isConnected)target.textContent=error.message;}
      }
    };
  };
})();
