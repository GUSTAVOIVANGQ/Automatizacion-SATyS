/* Keep /docs synchronized with the dashboard theme (localStorage key: theme). */
(() => {
  const THEME_KEY = 'theme';
  const root = document.documentElement;

  function normalizedTheme(value) {
    return value === 'dark' ? 'dark' : 'light';
  }

  function currentTheme() {
    try {
      return normalizedTheme(localStorage.getItem(THEME_KEY));
    } catch (_) {
      return 'light';
    }
  }

  function applyTheme(theme, persist = false) {
    const selected = normalizedTheme(theme);
    if (selected === 'dark') root.setAttribute('data-theme', 'dark');
    else root.removeAttribute('data-theme');

    if (persist) {
      try { localStorage.setItem(THEME_KEY, selected); } catch (_) {}
    }

    const button = document.getElementById('docs-theme-toggle');
    if (button) {
      const icon = button.querySelector('.docs-theme-icon');
      const label = button.querySelector('.docs-theme-label');
      const isDark = selected === 'dark';
      if (icon) icon.textContent = isDark ? '☀' : '☾';
      if (label) label.textContent = isDark ? 'Tema claro' : 'Tema oscuro';
      button.title = isDark ? 'Cambiar a tema claro' : 'Cambiar a tema oscuro';
      button.setAttribute('aria-label', button.title);
    }
  }

  function init() {
    applyTheme(currentTheme());
    const button = document.getElementById('docs-theme-toggle');
    if (button) {
      button.addEventListener('click', () => {
        const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        applyTheme(next, true);
      });
    }
  }

  window.addEventListener('storage', (event) => {
    if (event.key === THEME_KEY) applyTheme(event.newValue);
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
