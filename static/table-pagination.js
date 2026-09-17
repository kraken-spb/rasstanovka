(() => {
  'use strict';
  function windowFor(total, page, size = 50) {
    const pages = Math.max(1, Math.ceil(total / size));
    page = Math.max(0, Math.min(page, pages - 1));
    const start = page * size;
    const numbers = [...new Set([0, page - 1, page, page + 1, pages - 1])].filter(n => n >= 0 && n < pages).sort((a, b) => a - b);
    return {page, pages, start, end: Math.min(total, start + size), numbers};
  }
  function mount(target, label, change, {bottom = true} = {}) {
    const bars = (bottom ? ['top', 'bottom'] : ['top']).map(position => {
      const bar = document.createElement('nav');
      bar.className = 'table-pagination';
      bar.dataset.position = position;
      bar.setAttribute('aria-label', label + ': страницы (' + (position === 'top' ? 'сверху' : 'снизу') + ')');
      target[position === 'top' ? 'before' : 'after'](bar);
      return bar;
    });
    return {
      update(total, page, size, note = '') {
        const info = windowFor(total, page, size);
        for (const bar of bars) {
          const summary = document.createElement('span');
          summary.className = 'table-pagination-summary';
          summary.setAttribute('aria-live', 'polite');
          summary.textContent = (total ? info.start + 1 : 0) + '–' + info.end + ' из ' + total + ' · Страница ' + (info.page + 1) + ' из ' + info.pages;
          const controls = document.createElement('div');
          controls.className = 'table-pagination-pages';
          function button(text, destination, aria, disabled = false) {
            const node = document.createElement('button');
            node.type = 'button'; node.className = 'secondary-button'; node.textContent = text;
            node.setAttribute('aria-label', aria); node.disabled = disabled;
            node.dataset.page = destination;
            if (text === String(info.page + 1)) node.setAttribute('aria-current', 'page');
            node.addEventListener('click', async () => {
              if (await change(destination, size) === false) return;
              const replacement = bar.querySelector('[aria-current="page"]');
              replacement?.focus({preventScroll: true});
              if (bar.dataset.position === 'bottom') bars[0].scrollIntoView({block: 'nearest'});
            });
            controls.append(node);
          }
          button('‹', info.page - 1, 'Предыдущая страница', info.page === 0);
          let previous = -1;
          for (const number of info.numbers) {
            if (number > previous + 1) { const dots = document.createElement('span'); dots.textContent = '…'; controls.append(dots); }
            button(String(number + 1), number, 'Страница ' + (number + 1)); previous = number;
          }
          button('›', info.page + 1, 'Следующая страница', info.page === info.pages - 1);
          const label = document.createElement('label'); label.className = 'table-pagination-size'; label.append('По ');
          const select = document.createElement('select'); select.setAttribute('aria-label', 'Сотрудников на странице');
          for (const count of [25, 50, 100]) { const option = document.createElement('option'); option.value = count; option.textContent = count; select.append(option); }
          select.value = size;
          select.addEventListener('change', async () => {
            if (await change(0, Number(select.value)) === false) select.value = size;
            else bar.querySelector('select')?.focus({preventScroll: true});
          });
          label.append(select);
          bar.replaceChildren(summary, controls, label);
          if (note) { const hint = document.createElement('small'); hint.className = 'table-pagination-note'; hint.textContent = note; bar.append(hint); }
        }
        return info;
      }
    };
  }
  const api = {windowFor, mount};
  if (typeof module !== 'undefined') module.exports = api;
  if (typeof window !== 'undefined') window.TablePagination = api;
})();
