(() => {
  'use strict';
  const root = document.documentElement;
  const themes = [['lgss', 'Ленгазспецстрой', 'По умолчанию · сине-белая'], ['default', 'Классическая', 'Зелёные акценты']];
  let saving = false;
  window.appTheme = {
    busy: () => saving,
    createControl() {
      const fieldset = document.createElement('fieldset'); fieldset.className = 'profile-theme';
      const legend = document.createElement('legend'); legend.textContent = 'Тема оформления';
      const choices = document.createElement('div'); choices.className = 'theme-choices';
      const status = document.createElement('p'); status.className = 'profile-help'; status.setAttribute('role', 'status');
      status.textContent = 'Сохраняется автоматически в вашей учётной записи.';
      const radios = [];
      const sync = () => {radios.forEach(input => {input.checked = input.value === root.dataset.theme;}); fieldset.disabled = saving;};
      for (const [value, name, description] of themes) {
        const label = document.createElement('label'); label.className = 'theme-choice'; label.dataset.palette = value;
        const input = document.createElement('input'); input.type = 'radio'; input.name = 'app-theme'; input.value = value;
        const swatch = document.createElement('span'); swatch.className = 'theme-swatch'; swatch.setAttribute('aria-hidden', 'true');
        const caption = document.createElement('span'); const title = document.createElement('strong'); title.textContent = name;
        const detail = document.createElement('small'); detail.textContent = description; caption.append(title, detail);
        label.append(input, swatch, caption); choices.append(label); radios.push(input);
        input.addEventListener('change', async () => {
          if (saving || !input.checked || value === root.dataset.theme) return;
          const previous = root.dataset.theme; saving = true; root.dataset.theme = value; sync();
          status.textContent = 'Сохранение темы…'; status.classList.remove('error-text');
          try {
            const response = await fetch('/api/preferences/staffing', {method: 'PATCH',
              headers: {'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('.app-shell').dataset.csrf},
              body: JSON.stringify({theme: value})});
            if (!response.ok) throw new Error('Не удалось сохранить тему. Повторите выбор.');
            status.textContent = 'Тема «' + name + '» сохранена.';
          } catch (error) {
            root.dataset.theme = previous; status.textContent = error.message; status.classList.add('error-text');
          } finally {saving = false; sync();}
        });
      }
      sync(); fieldset.append(legend, choices, status); return fieldset;
    }
  };
})();
