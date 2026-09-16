(() => {
  'use strict';
  const trigger = document.getElementById('user-profile-open');
  const root = document.querySelector('.app-shell');
  if (!trigger || !root) return;
  const readOnly = root.dataset.role === 'hr_viewer';
  const blockedActions = new Set(['actions', 'import']);
  const allowedAction = action => !readOnly || !blockedActions.has(action.id);
  let current = null, dialog = null, saving = false;
  const roleNames = {super_admin: 'Супер-администратор', admin: 'Администратор', foreman: 'Ответственный', viewer: 'Просмотр', hr_viewer: 'Управление по работе с персоналом'};
  const el = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key in node) node[key] = value;
      else node.setAttribute(key, value);
    }
    children.flat().forEach(child => { if (child != null) node.append(child); });
    return node;
  };
  const display = combo => combo ? combo.replace('Key', '').replace('Digit', '').replaceAll('+', ' + ') : 'Отключено';
  async function api(method = 'GET', data) {
    const response = await fetch('/api/profile', {method, cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf},
      ...(data ? {body: JSON.stringify(data)} : {})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Не удалось сохранить профиль.');
    return result;
  }
  function close() { if (!saving) dialog?.close(); }
  async function openProfile() {
    if (dialog) { dialog.focus(); return; }
    const content = el('div', {className: 'profile-content'}, el('p', {}, 'Загрузка профиля…'));
    dialog = el('dialog', {className: 'user-profile-dialog', 'aria-labelledby': 'profile-title'},
      el('header', {className: 'profile-heading'}, el('h2', {id: 'profile-title'}, 'Мой профиль'),
        el('button', {type: 'button', className: 'secondary-button', onclick: close}, 'Закрыть')), content);
    const opened = dialog;
    dialog.addEventListener('cancel', event => { if (saving) event.preventDefault(); });
    dialog.addEventListener('close', () => { opened.remove(); if (dialog === opened) dialog = null; trigger.focus(); });
    document.body.append(dialog); dialog.showModal();
    try {
      const loaded = await api();
      if (dialog === opened) { current = loaded; render(content, loaded); }
    } catch (error) { if (dialog === opened) content.replaceChildren(el('p', {className: 'error-text'}, error.message)); }
  }
  function render(content, data) {
    const bindings = {...data.shortcuts};
    const message = el('p', {className: 'profile-status', role: 'status', 'aria-live': 'polite'});
    const phone = el('input', {id: 'profile-phone', type: 'tel', maxLength: 50, autocomplete: 'tel', value: data.phone});
    const email = el('input', {id: 'profile-email', type: 'email', maxLength: 254, autocomplete: 'email', value: data.email});
    const contact = el('input', {id: 'profile-contact', type: 'text', maxLength: 300, value: data.contact, placeholder: 'Например, Telegram или рабочий добавочный'});
    const enabled = el('input', {id: 'profile-shortcuts-enabled', type: 'checkbox', checked: data.enabled});
    const shortcuts = el('div', {className: 'profile-shortcuts'});
    const inputs = new Map();
    function updateValues() { inputs.forEach((input, id) => { input.value = display(bindings[id]); }); }
    for (const action of data.actions.filter(allowedAction)) {
      const input = el('input', {id: 'shortcut-' + action.id, className: 'shortcut-recorder', type: 'text', readOnly: true,
        value: display(bindings[action.id]), 'aria-describedby': 'profile-shortcut-help'});
      input.addEventListener('keydown', event => {
        if (readOnly) return;
        if (event.key === 'Tab' || event.key === 'Escape') return;
        event.preventDefault(); event.stopPropagation();
        if (event.repeat || event.isComposing || ['Control', 'Shift', 'Alt', 'Meta'].includes(event.key)) return;
        if (event.key === 'Backspace' || event.key === 'Delete') { bindings[action.id] = ''; updateValues(); return; }
        if (!event.ctrlKey || !event.shiftKey || event.altKey || event.metaKey || !/^(Key[A-Z]|Digit[0-9])$/.test(event.code) || data.reserved_codes.includes(event.code)) {
          message.textContent = 'Нажмите Ctrl + Shift + цифру или свободную букву. Это сочетание недоступно.'; return;
        }
        const combo = 'Ctrl+Shift+' + event.code;
        const duplicate = data.actions.find(other => other.id !== action.id && bindings[other.id] === combo);
        if (duplicate) { message.textContent = 'Сочетание уже назначено: ' + duplicate.label + '.'; return; }
        bindings[action.id] = combo; updateValues(); message.textContent = 'Сочетание изменено. Сохраните настройки.';
      });
      inputs.set(action.id, input);
      shortcuts.append(el('div', {className: 'profile-shortcut-row'}, el('label', {htmlFor: input.id}, action.label), input,
        el('button', {type: 'button', className: 'secondary-button', 'aria-label': 'Отключить: ' + action.label,
          onclick: () => { bindings[action.id] = ''; updateValues(); message.textContent = 'Сохраните настройки.'; }}, 'Отключить')));
    }
    const form = el('form', {className: 'profile-form'},
      el('div', {className: 'profile-identity'}, el('span', {className: 'profile-avatar', 'aria-hidden': 'true'}, data.user.full_name.slice(0, 1)),
        el('div', {}, el('strong', {}, data.user.full_name), el('p', {}, data.user.username + ' · ' + roleNames[data.user.role]))),
      el('fieldset', {disabled: readOnly}, el('legend', {}, 'Контактные данные'), el('div', {className: 'profile-contact-grid'},
        el('label', {htmlFor: phone.id}, 'Телефон', phone), el('label', {htmlFor: email.id}, 'Email', email),
        el('label', {htmlFor: contact.id, className: 'profile-contact-wide'}, 'Дополнительный контакт', contact))),
      el('fieldset', {}, el('legend', {}, 'Горячие клавиши'),
        el('label', {className: 'profile-enabled'}, enabled, 'Использовать горячие клавиши'),
        el('p', {id: 'profile-shortcut-help', className: 'profile-help'},
          'Выберите поле и нажмите Ctrl + Shift + цифру или букву. Сочетания работают по физическим клавишам и в русской раскладке. При вводе текста и в открытых диалогах они отключены. Escape закрывает диалоги, Ctrl + Z / Ctrl + Y сохраняют обычное поведение.'),
        shortcuts, el('button', {type: 'button', className: 'secondary-button', onclick: () => {
          data.actions.forEach(action => { bindings[action.id] = action.default; }); enabled.checked = true;
          updateValues(); message.textContent = 'Восстановлены стандартные сочетания. Сохраните настройки.';
        }}, 'Сбросить сочетания')),
      message, el('footer', {className: 'profile-footer'},
        el('button', {type: 'button', className: 'secondary-button', onclick: close}, 'Отмена'),
        el('button', {type: 'submit', className: 'primary-button'}, 'Сохранить профиль')));
    if (readOnly) {
      enabled.disabled = true;
      form.querySelectorAll('fieldset button, button[type="submit"]').forEach(button => { button.hidden = true; });
      form.querySelector('#profile-shortcut-help').textContent = 'Доступные сочетания клавиш для просмотра. Настройки профиля доступны только для чтения.';
      form.querySelector('.profile-footer button').textContent = 'Закрыть';
    }
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (readOnly || saving) return;
      saving = true; form.inert = true; message.textContent = 'Сохранение…';
      try {
        current = await api('PUT', {phone: phone.value, email: email.value, contact: contact.value,
          shortcuts: bindings, enabled: enabled.checked, token: data.token});
        data.token = current.token; message.textContent = 'Профиль и горячие клавиши сохранены.';
      } catch (error) { message.textContent = error.message; }
      finally { saving = false; form.inert = false; }
    });
    content.replaceChildren(form);
  }
  function run(action) {
    if (readOnly && blockedActions.has(action)) return false;
    if (action === 'profile') { openProfile(); return true; }
    const view = {staffing: 'staffing', employees: 'employees', summary: 'dashboard'}[action];
    if (view) {
      const button = document.querySelector('.desktop-nav [data-view="' + view + '"]');
      if (!button || button.disabled) return false;
      button.click(); return true;
    }
    if (action === 'search') {
      const field = [...document.querySelectorAll('.view.active input[type=search]')].find(input => input.getClientRects().length && !input.disabled);
      if (!field) return false; field.focus(); field.select(); return true;
    }
    if (!document.querySelector('#view-staffing.active') || !window.staffingScreen?.canLeave()) return false;
    const mobile = matchMedia('(max-width: 900px)').matches;
    const tools = document.getElementById('mobile-staffing-tools');
    if (mobile && tools) tools.open = true;
    const menu = action === 'actions' ? document.querySelector(mobile ? '#mobile-staffing-tools' : '.staffing-toolbar-actions')
      : document.getElementById('staffing-' + action);
    if (!menu || !menu.getClientRects().length || menu.querySelector('summary')?.matches(':disabled')) return false;
    if (action === 'actions' && mobile) { menu.scrollIntoView({block: 'nearest'}); return true; }
    menu.querySelector('summary')?.click(); return true;
  }
  document.addEventListener('keydown', event => {
    if (!current?.enabled || event.defaultPrevented || event.repeat || event.isComposing || !event.ctrlKey || !event.shiftKey || event.altKey || event.metaKey) return;
    if (event.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role=textbox]') || document.querySelector('dialog[open],.multi-filter-dialog')) return;
    const combo = 'Ctrl+Shift+' + event.code;
    const action = current.actions.find(item => current.shortcuts[item.id] === combo);
    if (action && run(action.id)) event.preventDefault();
  });
  trigger.addEventListener('click', openProfile);
  api().then(data => { current = data; }).catch(() => { /* Opening the profile provides retry and error feedback. */ });
})();
