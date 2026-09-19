(() => {
  'use strict';
  const DAY = 86400000;
  const iso = date => date.toISOString().slice(0, 10);
  const parse = value => /^(?!0000)\d{4}-\d{2}-\d{2}$/.test(value || '') && Number.isFinite(Date.parse(value)) && iso(new Date(value)) === value;
  const localToday = () => {const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
  const format = value => value ? value.split('-').reverse().join('.') : '';
  const clean = value => ({from:value?.from || '', to:value?.to || '', empty:value?.empty || '', values:[...(value?.values || [])]});
  const active = value => !!(value.from || value.to || value.empty || value.values?.length);
  function shiftMonth(value, amount) {
    const index=Math.max(0,Math.min(9999*12-1,(Number(value.slice(0,4))-1)*12+Number(value.slice(5,7))-1+amount));
    return `${String(Math.floor(index/12)+1).padStart(4,'0')}-${String(index%12+1).padStart(2,'0')}-01`;
  }
  function calendarDays(value) {
    const start=value.slice(0,7)+'-01',offset=(new Date(start).getUTCDay()+6)%7;
    return Array.from({length:42},(_,i)=>{const day=iso(new Date(Date.parse(start)+(i-offset)*DAY));return parse(day)?day:'';});
  }
  function preset(kind, today=localToday()) {
    if(kind==='today')return {from:today,to:today};
    if(kind==='yesterday'){const day=iso(new Date(Date.parse(today)-DAY));return {from:day,to:day};}
    if(kind==='week'){const start=Date.parse(today)-((new Date(today).getUTCDay()+6)%7)*DAY;return {from:iso(new Date(start)),to:iso(new Date(start+6*DAY))};}
    if(kind==='previous-month')return bounds('month',shiftMonth(today,-1).slice(0,7));
    if(kind==='quarter')return bounds('quarter',today.slice(0,4)+'-'+(Math.floor((Number(today.slice(5,7))-1)/3)+1));
    return bounds(kind,kind==='year'?today.slice(0,4):today.slice(0,7));
  }
  function bounds(mode, value) {
    if (mode === 'day') {if(!parse(value)) throw Error('Укажите корректную дату.');return {from:value, to:value};}
    const year = Number(value.slice(0, 4));
    if (!Number.isInteger(year) || year < 1 || year > 9999) throw Error('Укажите год от 1 до 9999.');
    let month = mode === 'year' ? 1 : mode === 'quarter' ? (Number(value.slice(5))-1)*3+1 : Number(value.slice(5));
    if (!Number.isInteger(month) || month < 1 || month > 12) throw Error('Укажите месяц или квартал.');
    const span = mode === 'year' ? 12 : mode === 'quarter' ? 3 : 1;
    const start = `${String(year).padStart(4,'0')}-${String(month).padStart(2,'0')}-01`;
    const end = new Date(start); end.setUTCMonth(month-1+span); end.setUTCDate(0);
    return {from:start,to:iso(end)};
  }
  function validate(value, {allowAll=true, maxDays=0}={}) {
    for (const day of [value.from,value.to,...(value.values || []).filter(v=>v !== '__none__')]) if (day && !parse(day)) return 'Укажите корректную дату.';
    if (value.from && value.to && value.from > value.to) return 'Дата «С» не может быть позже даты «По».';
    if (!allowAll && (!value.from || !value.to)) return 'Укажите начало и окончание периода.';
    if (maxDays && value.from && value.to && (Date.parse(value.to)-Date.parse(value.from))/DAY+1 > maxDays) return `Период — не более ${maxDays} дней.`;
    return '';
  }
  function caption(value) {
    if (value.values?.length) return `Выбрано дат: ${value.values.length}`;
    if (value.empty === 'only') return 'Без даты';
    if (value.empty === 'exclude' && !value.from && !value.to) return 'Дата заполнена';
    let text = value.from === value.to && value.from ? format(value.from) :
      value.from && value.to ? `${format(value.from)} — ${format(value.to)}` : value.from ? `С ${format(value.from)}` : value.to ? `По ${format(value.to)}` : 'Все даты';
    if (value.empty === 'include' && (value.from || value.to)) text += ' + без даты';
    return text;
  }
  function readQuery(query, key) {return clean({from:query.get(key+'_from'),to:query.get(key+'_to'),empty:query.get(key+'_empty'),values:query.getAll(key).filter(Boolean)});}
  function writeQuery(query, key, value) {
    for (const suffix of ['', '_from', '_to', '_empty']) query.delete(key+suffix);
    if (value.values?.length) value.values.forEach(item=>query.append(key,item));
    else for (const part of ['from','to','empty']) if (value[part]) query.set(key+'_'+part,value[part]);
  }
  const node = (tag, text, attrs={}) => {const el=document.createElement(tag); if(text) el.textContent=text; for(const [key,val] of Object.entries(attrs)) el.setAttribute(key,val); return el;};
  let sequence=0;
  function mount(config={}) {
    const {title='Период',allowEmpty=true,allowAll=true,maxDays=0,single=false,onChange=()=>{},canApply=()=>true,
      onApply=null,onClose=()=>{},extra=null,description='',applyLabel='Применить'}=config;
    let current=clean(config.value),available=[],dialog=null,submitting=false;
    const button=node('button','',{type:'button',class:'secondary-button date-filter-button','aria-haspopup':'dialog'});
    const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');
    icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');
    path.setAttribute('d','M4 5h16v16H4z M4 10h16 M8 3v4 M16 3v4');icon.append(path);
    const captionNode=node('span');button.append(icon,captionNode);
    const refresh=()=>{captionNode.textContent=caption(current);button.title=title+': '+caption(current);button.setAttribute('aria-label',button.title);button.classList.toggle('date-filter-active',active(current));};
    const close=()=>{if(dialog?.open&&!submitting)dialog.close();};
    function open() {
      if(dialog)return;
      const today=localToday(),initial=current.from||current.to||current.values.find(parse)||today;
      let viewMonth=initial.slice(0,7)+'-01',anchor=null,picker=false;
      let mode=single?'day':current.values.length?'values':current.empty==='only'?'empty':current.empty==='exclude'&&!current.from&&!current.to?'filled':'range';
      const selected=new Set(current.values),values=[...new Set([...available,...current.values])].sort();
      const id='date-filter-'+(++sequence);
      dialog=node('dialog','',{class:'date-filter-dialog','aria-labelledby':id});
      const form=node('form'),header=node('header'),heading=node('h2',title,{id}),dismiss=node('button','×',{type:'button','aria-label':'Закрыть календарь'});
      dismiss.addEventListener('click',close);header.append(heading,dismiss);
      const field=(text,input)=>{const label=node('label',text);label.append(input);return label;};
      const from=node('input','',{type:'date','aria-label':'С даты'}),to=node('input','',{type:'date','aria-label':'По дату'}),
        day=node('input','',{type:'date','aria-label':'Дата'}),month=node('input','',{type:'month','aria-label':'Месяц'}),
        year=node('input','',{type:'number',min:'1',max:'9999',step:'1','aria-label':'Год'}),quarter=node('select','',{'aria-label':'Квартал'});
      for(let i=1;i<=4;i++)quarter.append(new Option(i+' квартал',String(i)));
      from.value=current.from;to.value=current.to;day.value=initial;month.value=initial.slice(0,7);year.value=initial.slice(0,4);quarter.value=String(Math.floor((Number(initial.slice(5,7))-1)/3)+1);
      const controls=node('div','',{class:'date-filter-inputs'}),error=node('p','',{class:'error-text',role:'alert'}),hint=node('p','',{class:'date-filter-hint','aria-live':'polite'});
      const include=node('input','',{type:'checkbox'}),includeLabel=node('label','',{class:'date-filter-check'});include.checked=current.empty==='include';includeLabel.append(include,'Также без даты');
      const choices=node('div','',{class:'date-filter-values'}),search=node('input','',{type:'search',placeholder:'Найти дату','aria-label':'Найти дату в списке'});
      const modeButtons=node('div','',{class:'date-filter-modes','aria-label':'Способ выбора даты'}),special=node('div','',{class:'date-filter-special'});
      const modes=single?[['day','День']]:[['range','Период'],['day','День'],['month','Месяц'],['quarter','Квартал'],['year','Год'],...(values.length?[['values','Отдельные даты']]:[])];
      const specials=single?[]:[...(allowAll?[['all','Все даты']]:[]),...(allowEmpty?[['empty','Без даты'],['filled','Дата заполнена']]:[])];
      for(const [value,text] of [...modes,...specials]){
        const b=node('button',text,{type:'button','data-date-mode':value});
        b.addEventListener('click',()=>{mode=value;anchor=null;picker=false;render();});
        (specials.some(s=>s[0]===value)?special:modeButtons).append(b);
      }
      const quick=node('div','',{class:'date-filter-quick','aria-label':'Быстрые периоды'});
      for(const [label,kind] of [['Сегодня','today'],['Вчера','yesterday'],...(!single?[['Эта неделя','week'],['Этот месяц','month'],['Прошлый месяц','previous-month'],['Этот квартал','quarter'],['Этот год','year']]:[])]){
        const b=node('button',label,{type:'button'}),value=preset(kind,today),problem=validate(value,{maxDays,allowAll});
        if(problem){b.disabled=true;b.title=problem;}
        b.addEventListener('click',()=>{from.value=value.from;to.value=value.to;day.value=value.from;mode=single?'day':'range';viewMonth=value.from.slice(0,7)+'-01';anchor=null;picker=false;render();});quick.append(b);
      }
      const calendar=node('div','',{class:'date-filter-calendar'}),navigation=node('div','',{class:'date-filter-calendar-nav'}),
        prev=node('button','‹',{type:'button','aria-label':'Предыдущий месяц'}),next=node('button','›',{type:'button','aria-label':'Следующий месяц'}),
        jump=node('button','',{type:'button','aria-label':'Выбрать месяц и год'}),months=node('div','',{class:'date-filter-calendars'});
      navigation.append(prev,jump,next);calendar.append(navigation,months);
      prev.addEventListener('click',()=>{viewMonth=shiftMonth(viewMonth,picker?-12:-1);drawCalendar();});
      next.addEventListener('click',()=>{viewMonth=shiftMonth(viewMonth,picker?12:1);drawCalendar();});
      jump.addEventListener('click',()=>{picker=!picker;drawCalendar();});
      function draft() {
        if(mode==='range')return clean({from:from.value,to:to.value});
        if(mode==='day')return clean(bounds('day',day.value));
        if(['month','quarter','year'].includes(mode))return clean(bounds(mode,mode==='month'?month.value:String(year.value).padStart(4,'0')+(mode==='quarter'?'-'+quarter.value:'')));
        if(mode==='values')return clean({values:[...selected]});
        return clean({empty:mode==='empty'?'only':mode==='filled'?'exclude':''});
      }
      function choose(date) {
        if(mode==='values'){selected.has(date)?selected.delete(date):selected.add(date);renderChoices();drawCalendar(date);return;}
        if(mode==='day')day.value=date;
        else if(mode==='month')month.value=date.slice(0,7);
        else if(mode==='year')year.value=date.slice(0,4);
        else if(mode==='quarter'){year.value=date.slice(0,4);quarter.value=String(Math.floor((Number(date.slice(5,7))-1)/3)+1);}
        else{
          mode='range';
          if(!anchor){anchor=date;from.value=date;to.value=date;}
          else{from.value=date<anchor?date:anchor;to.value=date>anchor?date:anchor;anchor=null;}
        }
        render(date);
      }
      function drawCalendar(focusDate=null) {
        let value;try{value=draft();}catch(_){value=clean();}
        prev.disabled=viewMonth==='0001-01-01';next.disabled=viewMonth==='9999-12-01';
        prev.setAttribute('aria-label',picker?'Предыдущий год':'Предыдущий месяц');next.setAttribute('aria-label',picker?'Следующий год':'Следующий месяц');
        jump.textContent=picker?'К календарю':new Date(viewMonth).toLocaleDateString('ru-RU',{month:'long',year:'numeric',timeZone:'UTC'});
        months.replaceChildren();months.classList.toggle('date-filter-year-picker',picker);
        if(picker){
          const y=node('input','',{type:'number',min:'1',max:'9999',value:viewMonth.slice(0,4),'aria-label':'Год календаря'});
          y.addEventListener('change',()=>{const n=Number(y.value);if(Number.isInteger(n)&&n>=1&&n<=9999){viewMonth=String(n).padStart(4,'0')+viewMonth.slice(4);drawCalendar();}});
          const grid=node('div','',{class:'date-filter-month-picker'});months.append(field('Год',y),grid);
          for(let i=1;i<=12;i++){
            const date=viewMonth.slice(0,4)+'-'+String(i).padStart(2,'0')+'-01';
            const b=node('button',new Date(date).toLocaleDateString('ru-RU',{month:'long',timeZone:'UTC'}),{type:'button'});
            b.addEventListener('click',()=>{viewMonth=date;picker=false;drawCalendar();});grid.append(b);
          }
          return;
        }
        const visible=[viewMonth,shiftMonth(viewMonth,1)].filter((v,i,a)=>a.indexOf(v)===i);
        for(const start of visible){
          const panel=node('section','',{class:'date-filter-month','aria-label':new Date(start).toLocaleDateString('ru-RU',{month:'long',year:'numeric',timeZone:'UTC'})});
          panel.append(node('h3',panel.getAttribute('aria-label')));
          const grid=node('div','',{class:'date-filter-days'});
          for(const text of ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'])grid.append(node('span',text,{class:'date-filter-weekday','aria-hidden':'true'}));
          for(const date of calendarDays(start)){
            if(!date||date.slice(0,7)!==start.slice(0,7)){grid.append(node('span','',{class:'date-filter-padding','aria-hidden':'true'}));continue;}
            const isSelected=mode==='values'?selected.has(date):!!(value.from&&value.to&&date>=value.from&&date<=value.to);
            const b=node('button',String(Number(date.slice(8))),{type:'button','data-calendar-date':date,'aria-label':new Date(date).toLocaleDateString('ru-RU',{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}),'aria-pressed':String(isSelected),tabindex:date===(focusDate||day.value||today)?'0':'-1'});
            b.classList.toggle('date-filter-endpoint',date===value.from||date===value.to);b.classList.toggle('date-filter-in-range',isSelected);
            if(date===today)b.setAttribute('aria-current','date');
            if(mode==='values'&&!values.includes(date))b.disabled=true;
            b.addEventListener('click',()=>choose(date));
            b.addEventListener('keydown',event=>{
              const days={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7,Home:-((new Date(date).getUTCDay()+6)%7),End:6-((new Date(date).getUTCDay()+6)%7)};
              let target;
              if(event.key in days)target=iso(new Date(Date.parse(date)+days[event.key]*DAY));
              else if(['PageUp','PageDown'].includes(event.key)){const start=shiftMonth(date,event.key==='PageUp'?-1:1);target=start.slice(0,8)+String(Math.min(Number(date.slice(8)),Number(bounds('month',start.slice(0,7)).to.slice(8)))).padStart(2,'0');}
              else return;
              event.preventDefault();if(!parse(target))return;
              if(target.slice(0,7)!==viewMonth.slice(0,7))viewMonth=target.slice(0,7)+'-01';drawCalendar(target);
            });
            grid.append(b);
          }
          panel.append(grid);months.append(panel);
        }
        const enabled=[...months.querySelectorAll('button:not(:disabled)')];
        if(!enabled.some(b=>b.tabIndex===0)&&enabled.length)enabled[0].tabIndex=0;
        if(focusDate)enabled.find(b=>b.dataset.calendarDate===focusDate)?.focus({preventScroll:true});
      }
      function renderChoices(){
        choices.replaceChildren();
        values.filter(value=>format(value).includes(search.value)||value.includes(search.value)||value==='__none__'&&'без даты'.includes(search.value.toLowerCase())).forEach(value=>{
          const check=node('input','',{type:'checkbox'});check.checked=selected.has(value);
          check.addEventListener('change',()=>{check.checked?selected.add(value):selected.delete(value);drawCalendar();});
          const row=node('label','',{class:'date-filter-check'});row.append(check,value==='__none__'?'Без даты':format(value));choices.append(row);
        });
      }
      search.addEventListener('input',renderChoices);
      for(const input of [from,to,day,month,year,quarter])input.addEventListener('change',()=>{anchor=null;error.textContent='';const date=input.value;if(parse(date))viewMonth=date.slice(0,7)+'-01';else if([month,year,quarter].includes(input)){try{viewMonth=draft().from.slice(0,7)+'-01';}catch(_){}}drawCalendar();});
      function render(focusDate=null){
        error.textContent='';controls.replaceChildren();
        if(mode==='range')controls.append(field('С',from),field('По',to));
        if(mode==='day')controls.append(field('Дата',day));
        if(mode==='month')controls.append(field('Месяц',month));
        if(mode==='year')controls.append(field('Год',year));
        if(mode==='quarter')controls.append(field('Год',year),field('Квартал',quarter));
        if(mode==='values'){controls.append(search,choices);renderChoices();}
        includeLabel.hidden=!allowEmpty||single||!['range','day','month','quarter','year'].includes(mode);
        for(const b of [...modeButtons.children,...special.children])b.setAttribute('aria-pressed',String(b.dataset.dateMode===mode));
        hint.textContent=mode==='range'?(anchor?'Выберите конец периода.':'Первый клик — начало, второй — конец периода.')+(allowAll?' Можно оставить одну границу пустой.':''):mode==='values'?'Отмечайте нужные даты в календаре или списке.':'Выберите дату в календаре или введите её вручную.';
        drawCalendar(focusDate);
      }
      const actions=node('div','',{class:'date-filter-actions'}),cancel=node('button','Отмена',{type:'button',class:'secondary-button'}),apply=node('button',applyLabel,{type:'submit',class:'primary-button'});
      if(allowAll){const reset=node('button','Сбросить',{type:'button',class:'secondary-button'});reset.addEventListener('click',()=>{mode='range';from.value='';to.value='';include.checked=false;selected.clear();anchor=null;render();});actions.append(reset);}
      cancel.addEventListener('click',close);actions.append(cancel,apply);
      form.append(header);
      if(description)form.append(node('p',description,{class:'date-filter-hint'}));
      form.append(quick,modeButtons,controls,calendar,hint,special,includeLabel);
      if(extra)form.append(extra);
      form.append(error,actions);dialog.append(form);document.body.append(dialog);render();
      form.addEventListener('submit',async event=>{
        event.preventDefault();if(submitting)return;
        try{
          const next=draft();if(next.values.length>100)throw Error('Можно выбрать до 100 отдельных дат. Для большого периода используйте диапазон.');
          if(include.checked&&!includeLabel.hidden)next.empty='include';
          const problem=validate(next,{allowAll,maxDays});if(problem)throw Error(problem);
          if(!canApply())return;
          if(onApply){
            submitting=true;form.inert=true;apply.textContent='Сохранение…';error.textContent='';
            try{await onApply(clean(next));}
            finally{submitting=false;form.inert=false;apply.textContent=applyLabel;}
          }
          current=next;refresh();close();onChange(clean(current));
        }catch(err){error.textContent=err.message;}
      });
      dialog.addEventListener('cancel',event=>{if(submitting)event.preventDefault();});
      dialog.addEventListener('close',()=>{dialog.remove();dialog=null;button.focus();onClose();},{once:true});
      dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)close();}});
      dialog.showModal();jump.focus();return dialog;
    }
    button.addEventListener('click',open);refresh();
    return {button,open,close,get:()=>clean(current),set:value=>{current=clean(value);refresh();},setOptions:values=>{available=[...values];},active:()=>active(current)};
  }

  function group({fields,onChange=()=>{},canApply=()=>true}) {
    const element=node('details','',{class:'date-filter-group'}), summary=node('summary','Даты'), list=node('div','',{class:'date-filter-group-list'}), controls=new Map();
    const refresh=()=>{const count=[...controls.values()].filter(control=>control.active()).length;summary.textContent='Даты'+(count?' · '+count:'');};
    for(const [key,title] of fields){
      const field=node('label',title), control=mount({title,canApply,onChange:()=>{refresh();onChange();}});
      field.append(control.button);list.append(field);controls.set(key,control);
    }
    element.append(summary,list);
    document.addEventListener('click',event=>{if(!element.contains(event.target)&&!event.target.closest('.date-filter-dialog')) element.open=false;});
    element.addEventListener('keydown',event=>{if(event.key==='Escape'){element.open=false;summary.focus();}});
    return {element,controls,refresh,reset:()=>{controls.forEach(c=>c.set({}));refresh();},
      write:query=>controls.forEach((c,key)=>writeQuery(query,key,c.get())),read:query=>{controls.forEach((c,key)=>c.set(readQuery(query,key)));refresh();},
      count:()=>[...controls.values()].filter(c=>c.active()).length};
  }
  window.DateFilter={mount,group,bounds,validate,caption,readQuery,writeQuery,shiftMonth,calendarDays,preset};
})();
