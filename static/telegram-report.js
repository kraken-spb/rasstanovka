(() => {
  'use strict';
  const pdf = document.getElementById('placement-report-pdf');
  if (!pdf) return;
  const el = (tag, attrs = {}, ...children) => { const node=document.createElement(tag);Object.assign(node,attrs);node.append(...children);return node; };
  const open=el('button',{type:'button',className:'secondary-button'},'Telegram');
  pdf.after(open);
  const status=el('p',{role:'status'}), linkArea=el('div',{className:'telegram-link-area'});
  const refresh=el('button',{type:'button',className:'secondary-button'},'Проверить подключение');
  const link=el('button',{type:'button',className:'primary-button'},'Привязать мой Telegram');
  const unlink=el('button',{type:'button',className:'secondary-button',hidden:true},'Отключить мой Telegram');
  const token=el('input',{type:'password',autocomplete:'new-password',placeholder:'Токен от @BotFather',required:true,maxLength:150});
  token.setAttribute('aria-label','Токен Telegram-бота');
  const configure=el('button',{type:'submit',className:'primary-button'},'Подключить бота');
  const configForm=el('form',{className:'stack-form',hidden:true},
    el('h3',{},'Подключение бота для сайта'),
    el('p',{},'Создайте бота командой /newbot в ',el('a',{href:'https://t.me/BotFather',target:'_blank',rel:'noopener noreferrer'},'@BotFather'),
      ' и вставьте полученный токен. Используйте отдельного бота для отчётов.'),
    el('label',{},'Токен бота',token),configure,
    el('p',{className:'table-note'},'Токен хранится на сервере и не отображается после сохранения. Менять подключение может только супер-администратор.'));
  const close=el('button',{type:'button',className:'secondary-button'},'Закрыть');
  const dialog=el('dialog',{className:'app-dialog telegram-report-dialog'},el('h2',{},'Отчёты в Telegram'),
    el('p',{},'Бот отправляет отчёты в личный чат по запросу. Доступ к сотрудникам соответствует вашей учётной записи сайта.'),
    status,el('div',{className:'telegram-report-actions'},link,unlink,refresh),linkArea,
    el('details',{},el('summary',{},'Команды бота'),el('p',{},'/report — за сегодня; /report 13.09.2026 ППС15 Электромонтажник — с фильтрами; /report_full — со списком ФИО. Неявки показаны отдельно.')),
    configForm,close);
  dialog.setAttribute('aria-label','Отчёты в Telegram');document.body.append(dialog);
  let busy=false;
  const message=(text,error=false)=>{status.textContent=text;status.classList.toggle('error-text',error);};
  async function api(path,method='GET',body) {
    const response=await fetch('/api/telegram'+path,{method,cache:'no-store',headers:{'Content-Type':'application/json',
      'X-CSRF-Token':document.querySelector('main[data-csrf]').dataset.csrf},
      ...(body===undefined?{}:{body:JSON.stringify(body)})});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось выполнить запрос.');return data;
  }
  async function load() {
    message('Проверка подключения…');link.disabled=true;
    const data=await api('');configForm.hidden=!data.can_configure;unlink.hidden=!data.linked;
    link.hidden=!!data.linked;link.disabled=!data.configured;
    message(data.error || (!data.configured?'Бот пока не подключён. '+(data.can_configure?'Введите токен ниже.':'Обратитесь к супер-администратору.'):
      '@'+data.username+' · '+(data.online?'бот работает':'бот подключается')+' · '+(data.linked?'ваш Telegram привязан':'привяжите ваш Telegram')),!!data.error);
  }
  async function action(fn) {
    if(busy)return;busy=true;
    const buttons=[link,unlink,refresh,configure];buttons.forEach(b=>b.disabled=true);
    try{await fn();}catch(error){message(error.message,true);}finally{busy=false;refresh.disabled=false;configure.disabled=false;unlink.disabled=false;}
  }
  open.addEventListener('click',()=>{dialog.showModal();action(load);});
  close.addEventListener('click',()=>{dialog.close();token.value='';linkArea.replaceChildren();});
  dialog.addEventListener('close',()=>{token.value='';linkArea.replaceChildren();});
  refresh.addEventListener('click',()=>action(load));
  link.addEventListener('click',()=>action(async()=>{
    const data=await api('/link','POST',{});
    linkArea.replaceChildren(el('a',{href:data.url,target:'_blank',rel:'noopener noreferrer',className:'primary-button'},'Открыть бота и нажать «Старт»'),
      el('p',{className:'table-note'},'Персональная ссылка действует 10 минут. После запуска бота нажмите «Проверить подключение».'));
    message('Откройте персональную ссылку в своём Telegram.');link.disabled=false;
  }));
  unlink.addEventListener('click',()=>action(async()=>{await api('/link','DELETE');linkArea.replaceChildren();await load();}));
  configForm.addEventListener('submit',event=>{event.preventDefault();action(async()=>{
    message('Проверка токена в Telegram…');const secret=token.value.trim();token.value='';
    await api('/config','POST',{token:secret});linkArea.replaceChildren();await load();
  });});
})();
