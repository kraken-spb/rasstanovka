(() => {
  'use strict';
  const root = document.querySelector('.app-shell');
  if (!root || !document.querySelector('#view-workforce') || !['admin','super_admin','rotation','recruitment'].includes(root.dataset.role)) return;
  const E = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key,value] of Object.entries(attrs)) {
      if (key.startsWith('on')) node.addEventListener(key.slice(2),value);
      else if (key in node) node[key] = value;
      else node.setAttribute(key,value);
    }
    for (const child of children.flat()) if (child != null) node.append(child);
    return node;
  };
  let preview = null, busy = false, offset = 0;
  const decisions = new Map(), size = 50;
  const errors = E('p',{className:'error-text',role:'alert',hidden:true});
  const feedback = E('p',{role:'status'}), content = E('div'), confirm = E('input',{type:'checkbox'});
  const apply = E('button',{type:'button',className:'primary-button',disabled:true,onclick:applyImport},'Подтвердить и применить');
  const date = E('input',{type:'date',required:true}), source = E('select',{required:true});
  const file = E('input',{type:'file',accept:'.xlsx',required:true});
  const submit = E('button',{type:'submit',className:'primary-button'},'Проверить файл');
  const close = E('button',{type:'button',className:'secondary-button','aria-label':'Закрыть импорт',onclick:() => {if (!busy) dialog.close();}},'Закрыть');
  const form = E('form',{className:'wf-form',onsubmit:previewImport},
    E('label',{},'Источник',source),E('label',{},'Отчётная дата',date),E('label',{className:'wf-wide'},'Файл Excel',file),submit);
  const consent = E('label',{className:'check-label'},confirm,'Подтверждаю добавления, изменения и исключения из состава источника');
  const footer = E('div',{className:'wf-import-apply',hidden:true},consent,apply);
  const dialog = E('dialog',{className:'wf-card','aria-labelledby':'wf-import-title'},
    E('header',{className:'wf-card-heading'},E('h2',{id:'wf-import-title'},'Сверка импорта'),close),
    E('p',{},'Импорт шаблона перевахтовки или файла комплектации. Сначала проверьте итог сверки. Категории ГДЛР сохраняются по базе. Ручные записи и история сохраняются.'),
    form,feedback,errors,content,footer);
  document.body.append(dialog);
  dialog.addEventListener('cancel',event => {if (busy) event.preventDefault();});
  confirm.addEventListener('change',canApply);
  function error(message) {errors.textContent = message || '';errors.hidden = !message;}
  function lock(value) {busy = value;form.inert = value;close.disabled = value;footer.inert = value;content.inert = value;}
  async function api(path, options = {}) {
    const response = await fetch('/api/workforce/imports/' + path,{...options,cache:'no-store',
      headers:{'X-CSRF-Token':root.dataset.csrf,...(options.body instanceof FormData ? {} : {'Content-Type':'application/json'})}});
    return window.readApiResponse(response, 'Не удалось выполнить сверку.');
  }
  const button = E('button',{type:'button',className:'secondary-button',onclick:async () => {
    date.value = document.querySelector('#wf-date').value;
    error('');dialog.showModal();
    try {if (!source.options.length) {const values = await api('sources');source.replaceChildren(...values.map(row => E('option',{value:row.key},row.label)));}}
    catch (err) {error(err.message);}
  }},'Импорт');
  document.querySelector('.wf-heading').append(button);
  function canApply() {apply.disabled = !preview || preview.state === 'applied' || !confirm.checked || preview.items.some(item => item.restricted_identity || item.issue &&
    (!decisions.get(item.index)?.worker_id || !decisions.get(item.index)?.reason.trim()));}
  async function previewImport(event) {
    event.preventDefault();if (busy || !form.reportValidity()) return;
    const data = new FormData();data.append('file',file.files[0]);data.append('source_key',source.value);data.append('date',date.value);
    lock(true);error('');preview = null;content.replaceChildren();footer.hidden = true;feedback.textContent = 'Чтение файла и сверка с базой…';
    try {preview = await api('preview',{method:'POST',body:data});offset = 0;decisions.clear();confirm.checked = false;render();}
    catch (err) {error(err.message);feedback.textContent = '';}
    finally {lock(false);}
  }
  function render() {
    const counts = preview.counts;
    feedback.textContent = preview.state === 'applied' ? 'Этот файл на эту дату уже применён. Повторных записей не создано.' :
      `Новых: ${counts.added || 0} · Найдено в базе: ${counts.matched || 0} · Требуют решения: ${counts.review || 0} · Отсутствуют в файле: ${preview.missing.length}. Вне доступных СМУ: ${preview.skipped_count}.`;
    content.replaceChildren();footer.hidden = preview.state === 'applied';
    if (preview.state === 'applied') return;
    const pager = E('div',{className:'wf-list-heading'},E('span',{},`${offset+1}–${Math.min(offset+size,preview.items.length)} из ${preview.items.length}`),
      E('div',{},E('button',{type:'button',disabled:offset === 0,onclick:() => {offset -= size;render();}},'Назад'),
        E('button',{type:'button',disabled:offset+size >= preview.items.length,onclick:() => {offset += size;render();}},'Далее')));
    content.append(pager);
    for (const item of preview.items.slice(offset,offset+size)) {
      const block = E('details',{className:'wf-entry',open:!!item.issue},E('summary',{},item.name + ' · ' + (item.tab || 'без табельного') + ' · ' +
        (item.issue ? 'Требует решения' : item.worker_id ? 'Есть в базе' : 'Новый')),
        E('p',{},item.department || 'СМУ не указан'));
      if (item.category_preserved) block.append(E('p',{},'ГДЛР сохранится: ' + item.category_preserved));
      for (const change of item.changes) block.append(E('p',{},({name:'ФИО',profession:'Должность',employer:'Работодатель'}[change.field] || change.field) + ': ' + change.before + ' → ' + change.after));
      for (const warning of item.warnings) block.append(E('p',{},warning));
      if (item.restricted_identity) {
        block.append(E('p',{className:'error-text'},item.issue));
      } else if (item.issue) {
        const current = decisions.get(item.index) || {worker_id:'',reason:''};
        decisions.set(item.index,current);
        const selection = E('select',{},E('option',{value:''},'Выберите решение'),
          ...item.candidates.map(row => E('option',{value:String(row.id)},row.name + ' · ' + (row.tab || 'без табельного'))),
          E('option',{value:'new'},'Подтверждаю: это новый сотрудник'));
        selection.value = String(current.worker_id);
        const selectedChanges = E('div');
        const showChoice = () => {
          const candidate = item.candidates.find(row=>row.id===current.worker_id);
          selectedChanges.replaceChildren(...(candidate?.changes||[]).map(change=>E('p',{},({name:'ФИО',profession:'Должность',employer:'Работодатель'}[change.field]||change.field)+': '+change.before+' → '+change.after)),
            ...(candidate?.warnings||[]).map(warning=>E('p',{},warning)));
        };
        selection.addEventListener('change',() => {current.worker_id = selection.value === 'new' ? 'new' : selection.value ? Number(selection.value) : '';confirm.checked=false;showChoice();canApply();});
        showChoice();
        const reason = E('textarea',{value:current.reason,maxLength:10000});
        reason.addEventListener('input',() => {current.reason = reason.value;canApply();});
        block.append(E('p',{className:'error-text'},item.issue),E('label',{},'Сопоставление',selection),selectedChanges,E('label',{},'Основание решения',reason));
      }
      content.append(block);
    }
    const missing = E('details',{className:'wf-entry'},E('summary',{},'Не найдены в файле: ' + preview.missing.length),
      E('p',{},'Исключение касается состава этого источника. Карточки, назначения и история сохраняются. Ручные записи остаются в составе.'));
    const missingRows = E('div');let visible = 0;
    const more = E('button',{type:'button'},'Показать ещё');
    function appendMissing() {for (const row of preview.missing.slice(visible,visible+50)) missingRows.append(E('p',{},row.full_name + (row.manually_created ? ' · ручная запись, сохраняется' : ' · исключить из источника')));visible += 50;more.hidden = visible >= preview.missing.length;}
    more.addEventListener('click',appendMissing);appendMissing();missing.append(missingRows,more);content.append(missing);
    const mapping = E('details',{className:'wf-entry'},E('summary',{},'Сопоставление столбцов и исключения'),E('p',{},'Исключённых листов и строк: ' + preview.excluded_count));
    for (const sheet of preview.mappings) mapping.append(E('details',{},E('summary',{},sheet.sheet),...sheet.columns.map(column => E('p',{},column.column + ' · ' + column.header + ' → ' + (column.field || 'только исходная строка')))));
    content.append(mapping);canApply();
  }
  async function applyImport() {
    if (busy || apply.disabled) return;
    lock(true);error('');feedback.textContent = 'Резервная копия и применение подтверждённых изменений…';
    try {
      const result = await api(preview.id + '/apply',{method:'POST',body:JSON.stringify({token:preview.token,confirmed:true,
        decisions:[...decisions.entries()].filter(([,value]) => value.worker_id).map(([index,value]) => ({index,...value}))})});
      preview.state = 'applied';footer.hidden = true;content.replaceChildren();
      feedback.textContent = `Импорт завершён. Добавлено: ${result.added}. Сопоставлено: ${result.matched}. Исключено из источника: ${result.removed_from_source}. Сохранено ручных: ${result.manual_preserved}.`;
      await window.workforceScreen?.load();
    } catch (err) {error(err.message);feedback.textContent = 'Изменения не подтверждены. При сетевой ошибке повторное нажатие безопасно: импорт не задвоится.';}
    finally {lock(false);}
  }
})();
