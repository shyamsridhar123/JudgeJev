// Run before the stylesheet so a saved theme is applied before the page paints.
// This stays independent of evidence loading, including its error state.
(() => {
  const root = document.documentElement;
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const storageKey = 'judgejev-theme';
  let preference = null;

  try {
    const saved = localStorage.getItem(storageKey);
    if (saved === 'light' || saved === 'dark') preference = saved;
  } catch {
    // Private or restricted browsers can still use the switch for this visit.
  }

  function applyTheme() {
    const dark = (preference ?? (system.matches ? 'dark' : 'light')) === 'dark';
    root.dataset.theme = dark ? 'dark' : 'light';
    const toggle = document.getElementById('theme-toggle');
    if (toggle) {
      toggle.setAttribute('aria-pressed', String(dark));
      toggle.title = dark ? 'Switch to light mode' : 'Switch to dark mode';
    }
  }

  applyTheme();
  system.addEventListener('change', applyTheme);

  document.addEventListener('DOMContentLoaded', () => {
    const toggle = document.getElementById('theme-toggle');
    applyTheme();
    toggle.hidden = false;
    toggle.addEventListener('click', () => {
      preference = root.dataset.theme === 'dark' ? 'light' : 'dark';
      try {
        localStorage.setItem(storageKey, preference);
      } catch {
        // Persistence is optional; applying the theme must still work.
      }
      applyTheme();
    });
  }, {once: true});
})();
