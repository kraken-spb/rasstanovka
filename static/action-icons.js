(() => {
  'use strict';
  const selector = 'button, summary, a.primary-button, a.secondary-button';
  const navigation = {staffing:'placement',verification:'verify',employees:'people',outstaff:'group-add',dashboard:'columns',analytics:'chart',plan:'calendar',catalogs:'catalog',accounts:'accounts',logs:'logs',backups:'backup'};
  const rules = [
    [/^Выйти$/, 'logout'], [/^Войти$/, 'login'], [/^(Закрыть|Отмена|Отменить ввод|×|✕)/, 'close'],
    [/^Предыдущая неделя|^Назад$|^‹$/, 'left'], [/^Следующая неделя|^Далее$|^›$/, 'right'],
    [/^Перенести со вчера/, 'previous-day'], [/^Перенести на завтра/, 'next-day'],
    [/^Снять подтверждение/, 'uncheck'], [/^Подтвердить/, 'verify'],
    [/^Сбросить фильтры|^Снять фильтр/, 'filter-reset'], [/^Сбросить расстановку|^Без места работы/, 'uncheck'],
    [/^Снять выделение|^Сбросить$|^Очистить/, 'close'], [/^Вернуть исходный вид|^Восстановить/, 'restore'],
    [/^Свернуть/, 'collapse'], [/^Развернуть/, 'expand'], [/^Отменить/, 'undo'], [/^Повторить/, 'redo'],
    [/^Обновить|^Проверить подключение/, 'refresh'], [/^Удалить/, 'trash'],
    [/^Редактировать|^Изменить ФИО|^Изменить$|^Переименовать/, 'edit'], [/^Пароль$|^Сменить пароль/, 'key'], [/^Изменить ГД[Т]?ЛР|^Категория ГД[Т]?ЛР$/, 'category'],
    [/^Работодатель(?:\s|\(|$)|^Выполняемые работы/, 'work'], [/^Сохранить/, 'save'], [/^Применить|^Готово$/, 'check'],
    [/^Добавить сотрудника|^Добавить людей/, 'person-add'], [/^Создать бригаду/, 'group-add'],
    [/^Создать бэкап|^Создать копию/, 'backup'], [/^Создать учётную запись|^Назначить СМУ/, 'accounts'],
    [/^Добавить|^Создать/, 'plus'], [/^Импорт/, 'upload'],
    [/PDF|^Карточки позиций/, 'pdf'], [/Excel|XLSX/, 'excel'], [/^Экспорт|^Скачать/, 'download'],
    [/^Проверить файл|^Проверить и сопоставить/, 'file'], [/^Показать$|^Найти|^Поиск$/, 'search'],
    [/^Выбрать найденные|^Выбрать всех/, 'select-all'], [/^Столбцы$/, 'columns'],
    [/^Действия$|^Ещё/, 'more'], [/^Telegram$|^Подключить бота|^Открыть бота/, 'send'],
    [/^Привязать/, 'link'], [/^Отключить/, 'unlink'], [/^Как у бригады/, 'people'],
    [/^Ответственные бригады|^Ответственные$/, 'people'], [/^Все поля$/, 'eye'],
    [/^К выбранным сотрудникам|^Выбрать место работы/, 'pin'], [/^Как считается отчёт$/, 'info'],
  ];
  const metadata = new WeakMap();
  // Keep native checkbox state, keyboard behavior and saved filter preferences.
  for (const [id, icon, title] of [
    ['staffing-regex', 'regex', 'Regex — поиск по шаблону'],
    ['employees-regex', 'regex', 'Regex — поиск по шаблону'],
    ['outstaff-regex', 'regex', 'Regex — поиск по шаблону'],
    ['staffing-unassigned', 'person-minus', 'Показать только нерасставленных сотрудников'],
  ]) {
    const input = document.getElementById(id);
    const label = input?.closest('label');
    if (!label) continue;
    label.classList.add('ui-filter-toggle');
    label.dataset.uiIcon = icon;
    label.title = title;
    input.setAttribute('aria-label', title);
  }
  function decorate(control) {
    if (control.closest('.table-pagination, #catalog-rail') || control.id === 'catalog-rail-backdrop' || control.matches('.multi-filter-button, [data-display]')) return;
    const text = control.textContent.replace(/\s+/g, ' ').trim();
    const previous = metadata.get(control);
    const clean = text.replace(/^[+＋]\s*/, '');
    const sectionIcon = {categories:'category',smu:'work',contractors:'people',stages:'calendar',groups:'catalog',subobjects:'pin'}[control.dataset.catalog] ||
      {week:'calendar',date:'columns',category:'category',placement:'placement'}[control.dataset.summaryMode];
    const icon = navigation[control.dataset.view] || sectionIcon || rules.find(([pattern]) => pattern.test(clean))?.[1] ||
      (control.getAttribute('aria-label') === 'Выйти' ? 'logout' : previous?.icon);
    if (!icon) return;
    // Leave the original text nodes intact: existing handlers still own labels and counters.
    const currentAria = control.getAttribute('aria-label');
    const label = currentAria && currentAria !== previous?.aria ? currentAria : text;
    const currentTitle = control.getAttribute('title');
    const title = currentTitle && currentTitle !== previous?.title ? currentTitle : label;
    const retainLabel = Boolean(control.dataset.view || control.closest('dialog, #wf-card, .login-card, .multi-filter-dialog, .transfer-window, .staffing-toolbar-actions-content, .mobile-panel-content, .mobile-navigation-items, .mobile-disclosure') || control.matches('[data-summary-mode], [data-catalog], [data-display]'));
    control.dataset.uiIcon = icon;
    control.classList.add('ui-action');
    control.classList.toggle('ui-icon-only', !retainLabel);
    control.classList.toggle('ui-icon-label', retainLabel);
    control.setAttribute('aria-label', label);
    control.setAttribute('title', title);
    const count = clean.match(/\((\d+)\)$/)?.[1];
    if (count && count !== '0' && !retainLabel) control.dataset.actionCount = count;
    else delete control.dataset.actionCount;
    metadata.set(control, {icon, aria:label, title});
  }
  function collect(node, controls) {
    if (node.nodeType !== Node.ELEMENT_NODE) {
      const owner = node.parentElement?.closest(selector);
      if (owner) controls.add(owner);
      return;
    }
    const owner = node.closest(selector);
    if (owner) controls.add(owner);
    node.querySelectorAll(selector).forEach(control => controls.add(control));
  }
  const initial = new Set(); collect(document.body, initial); initial.forEach(decorate);
  new MutationObserver(records => {
    const controls = new Set();
    for (const record of records) {
      if (record.type !== 'childList') collect(record.target, controls);
      else {
        const owner = record.target.closest?.(selector);
        if (owner) controls.add(owner);
        record.addedNodes.forEach(node => collect(node, controls));
      }
    }
    controls.forEach(control => { if (control.isConnected) decorate(control); });
  }).observe(document.body, {subtree:true, childList:true, characterData:true});
})();
