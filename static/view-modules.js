(() => {
  'use strict';
  const pending = new Map();
  function load(marker) {
    const url=marker.dataset.src;
    if (pending.has(url)) return pending.get(url);
    const promise=new Promise((resolve,reject)=>{
      const script=document.createElement('script');script.src=url;script.async=false;
      script.onload=()=>resolve();
      script.onerror=()=>{script.remove();pending.delete(url);reject(new Error('Не удалось загрузить раздел. Повторите открытие.'));};
      document.head.append(script);
    });
    pending.set(url,promise);return promise;
  }
  window.loadViewModules=async view=>{
    // Keep original dependency order; repeated and concurrent navigation loads once.
    for (const marker of document.querySelectorAll('script[data-view-module]')) {
      if (marker.dataset.viewModule.split(' ').includes(view)) await load(marker);
    }
  };
})();
