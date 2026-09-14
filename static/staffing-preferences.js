(() => {
  'use strict';
  const initial = document.getElementById('staffing-preferences-data');
  if (!initial) return;
  const root = document.querySelector('.app-shell');
  const notice = document.getElementById('staffing-preferences-status');
  const outboxKey = 'crewplacement.staffing-pending.v1.' + root.dataset.userId;
  const settings = JSON.parse(initial.textContent);
  let pending = {}, inFlight = null, saving = false, timer;
  function message(text, failed = false) {
    notice.textContent = text; notice.classList.toggle('error-text', failed);
  }
  try {
    const saved = JSON.parse(localStorage.getItem(outboxKey));
    if (saved && typeof saved === 'object' && !Array.isArray(saved)) pending = saved;
  } catch (_) { message('Браузер не разрешил прочитать несохранённые настройки.', true); }
  Object.assign(settings, pending);
  function remember() {
    try {
      const remaining = {...inFlight, ...pending};
      if (Object.keys(remaining).length) localStorage.setItem(outboxKey, JSON.stringify(remaining));
      else localStorage.removeItem(outboxKey);
    } catch (_) { /* The authenticated server remains the primary storage. */ }
  }
  async function flush() {
    clearTimeout(timer);
    if (saving || !Object.keys(pending).length) return;
    saving = true;
    inFlight = pending; pending = {}; remember();
    message('Сохранение настроек…');
    let success = false;
    try {
      const response = await fetch('/api/preferences/staffing', {method: 'PATCH', keepalive: true,
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf}, body: JSON.stringify(inFlight)});
      if (!response.ok) throw new Error('Не удалось сохранить настройки в учётной записи. Повторим при следующем изменении или открытии страницы.');
      success = true; message('Настройки сохранены в вашей учётной записи.');
    } catch (error) {
      pending = {...inFlight, ...pending};
      message(error.message, true);
    } finally {
      inFlight = null; saving = false; remember();
      if (success && Object.keys(pending).length) flush();
    }
  }
  window.staffingPreferences = {
    get: (key, fallback) => settings[key] === undefined ? fallback : structuredClone(settings[key]),
    set(patch, {debounce = false} = {}) {
      const changed = Object.fromEntries(Object.entries(patch).filter(([key, value]) => JSON.stringify(settings[key]) !== JSON.stringify(value)));
      if (!Object.keys(changed).length) { if (Object.keys(pending).length) flush(); return; }
      Object.assign(settings, structuredClone(changed)); Object.assign(pending, structuredClone(changed));
      remember();
      if (debounce) { clearTimeout(timer); timer = setTimeout(flush, 350); }
      else flush();
    },
    flush
  };
  window.addEventListener('online', flush);
  window.addEventListener('pagehide', () => { remember(); flush(); });
  flush();
})();
