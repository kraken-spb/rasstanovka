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
            const data = await window.readApiResponse(response, 'Не удалось загрузить справочник объектов.');
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

// Page-local catalog data: never persisted across accounts or browser sessions.
(() => {
  'use strict';
  const entries = new Map();
  const lifetime = 60 * 1000;
  window.catalogData = {
    invalidate() { entries.clear(); },
    async get(key, read, {refresh = false} = {}) {
      if (refresh) entries.delete(key);
      let entry = entries.get(key);
      if (!entry || Date.now() >= entry.expires) {
        entry = {expires: Infinity};
        entries.set(key, entry);
        entry.promise = Promise.resolve().then(read).then(data => {
          entry.expires = Date.now() + lifetime;
          return data;
        }).catch(error => {
          if (entries.get(key) === entry) entries.delete(key);
          throw error;
        });
      }
      // Renderers may sort arrays or attach edit tokens to rows.
      return structuredClone(await entry.promise);
    }
  };
  window.addEventListener('focus', () => window.catalogData.invalidate());
})();
