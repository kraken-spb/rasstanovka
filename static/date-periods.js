(() => {
  'use strict';

  const iso = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : '';
  const changed = input => input.dispatchEvent(new Event('change', {bubbles: true}));

  function setupSingleDates() {
    const ids=['staffing-date','work-date','wf-date','employees-date','outstaff-date','analytics-detail-date',
      'user-activity-date','dashboard-start','report-date','placement-report-date','plan-start','backups-date','rs-date','rs-end1','rs-end2'];
    for(const id of ids){
      const input=document.getElementById(id);if(!input||input.dataset.calendarMounted)continue;
      input.dataset.calendarMounted='true';
      const title=input.closest('label')?.textContent.trim()||'Дата';
      const sync=()=>{control.set({from:iso(input.value),to:iso(input.value)});control.button.disabled=input.disabled||input.readOnly;};
      const control=window.DateFilter.mount({title,single:true,allowAll:false,allowEmpty:false,value:{from:iso(input.value),to:iso(input.value)},
        canApply:()=>!input.disabled&&!input.readOnly,onChange(value){input.value=value.from;changed(input);sync();}});
      control.button.dataset.dateInput=id;input.after(control.button);input.hidden=true;
      // Keep the caption in sync with restored preferences, report drilldowns and rejected edits.
      const descriptor=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');
      Object.defineProperty(input,'value',{configurable:true,get(){return descriptor.get.call(this);},set(value){descriptor.set.call(this,value);sync();}});
      input.addEventListener('change',sync);input.form?.addEventListener('reset',()=>queueMicrotask(sync));
      new MutationObserver(sync).observe(input,{attributes:true,attributeFilter:['disabled','readonly','value']});
      control.button.addEventListener('click',sync,true);sync();
    }
  }

  function mountRange({title, from, to, maxDays, onChange, after, hidden = [], allowAll = false}) {
    if (!from || !to || !window.DateFilter?.mount) return null;
    let syncing = false;
    const control = window.DateFilter.mount({
      title,
      value: {from: iso(from.value), to: iso(to.value)},
      allowEmpty: false,
      allowAll,
      maxDays,
      onChange(value) {
        if (syncing) return;
        syncing = true;
        from.value = iso(value?.from);
        to.value = iso(value?.to);
        onChange();
        syncing = false;
      },
    });
    const field = document.createElement('div');
    field.className = 'date-period-field';
    const caption = document.createElement('span'); caption.textContent = title;
    field.append(caption, control.button); after.after(field);
    control.field = field;
    hidden.forEach(label => { label.hidden = true; });
    const syncControl = () => {
      if (syncing) return;
      syncing = true;
      control.set({from: iso(from.value), to: iso(to.value)});
      syncing = false;
    };
    from.addEventListener('change', syncControl);
    to.addEventListener('change', syncControl);
    from.form?.addEventListener('reset', () => queueMicrotask(syncControl));
    control.button.addEventListener('click', syncControl, true);
    return control;
  }

  function setupAnalytics() {
    const from = document.getElementById('analytics-start');
    const to = document.getElementById('analytics-end');
    if (!from || !to) return;
    mountRange({title: 'Период динамики', from, to, maxDays: 366, after: to.closest('label'),
      hidden: [from.closest('label'), to.closest('label')], onChange() { changed(to); }});
  }

  function setupPlacement() {
    const root = document.getElementById('placement-report');
    const from = document.getElementById('placement-report-start');
    const to = document.getElementById('placement-report-date');
    if (!root || !from || !to) return;
    const fromLabel = from.closest('label'), toLabel = to.closest('label');
    const control = mountRange({title: 'Период отчёта по расстановке', from, to, maxDays: 366,
      after: toLabel, hidden: [fromLabel, toLabel], onChange() { changed(to); }});
    if (!control) return;
    const updateMode = () => {
      const category = root.classList.contains('category-mode');
      control.field.hidden = category;
      toLabel.hidden = !category;
      fromLabel.hidden = true;
    };
    new MutationObserver(updateMode).observe(root, {attributes: true, attributeFilter: ['class']});
    updateMode();
  }

  function setupLogs() {
    const form = document.getElementById('logs-form');
    const exact = form?.querySelector('[name=work_date]');
    if (!form || !exact) return;
    const eventFrom = form.querySelector('[name=from]'), eventTo = form.querySelector('[name=to]');
    mountRange({title: 'Время события', from: eventFrom, to: eventTo, maxDays: 0, allowAll: true,
      after: eventTo.closest('label'), hidden: [eventFrom.closest('label'), eventTo.closest('label')], onChange() {
        if (window.logsScreen?.load) window.logsScreen.load();
      }});
    const from = document.createElement('input'), to = document.createElement('input');
    from.type = to.type = 'hidden'; from.name = 'work_date_from'; to.name = 'work_date_to';
    from.value = to.value = exact.value;
    form.append(from, to);
    // Hidden inputs reflect .value into their default value, so native reset
    // alone would retain the last selected period in the next request.
    form.addEventListener('reset', () => { from.value = to.value = ''; });
    mountRange({title: 'Дата расстановки', from, to, maxDays: 0, allowAll: true,
      after: exact.closest('label'), hidden: [exact.closest('label')], onChange() {
      exact.value = '';
      if (window.logsScreen?.load) window.logsScreen.load();
    }});
  }

  function start(attempt = 0) {
    if (!window.DateFilter?.mount) {
      if (attempt < 20) window.setTimeout(() => start(attempt + 1), 50);
      return;
    }
    setupAnalytics(); setupPlacement(); setupLogs();setupSingleDates();
  }

  start();
})();
