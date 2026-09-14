(() => {
  'use strict';
  let pending = null;
  let expiresAt = 0;
  const cacheLifetime = 60 * 1000;
  window.appReference = {
    invalidate() { pending = null; expiresAt = 0; },
    get({refresh = false} = {}) {
      if (refresh || Date.now() >= expiresAt) pending = null;
      if (!pending) {
        expiresAt = Infinity;
        const request = fetch('/api/reference?scope=locations', {cache: 'no-cache'})
          .then(async response => {
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Не удалось загрузить справочник объектов.');
            if (pending === request) expiresAt = Date.now() + cacheLifetime;
            return data;
          });
        pending = request;
        request.catch(() => { if (pending === request) { pending = null; expiresAt = 0; } });
      }
      return pending;
    }
  };
})();
