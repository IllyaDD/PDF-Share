// Світла/темна тема через data-bs-theme Bootstrap 5.3.
// Підключається в <head> синхронно, до CSS: тема ставиться до першого рендеру,
// інакше сторінка спершу блимає світлою. Вибір користувача — у localStorage;
// без нього тема йде за системною (prefers-color-scheme) і стежить за її зміною.
(() => {
  const KEY = "theme";
  const root = document.documentElement;
  const system = window.matchMedia("(prefers-color-scheme: dark)");

  // localStorage недоступний у приватному режимі частини браузерів — тоді тема лише системна.
  const stored = () => {
    try {
      const value = localStorage.getItem(KEY);
      return value === "dark" || value === "light" ? value : null;
    } catch {
      return null;
    }
  };

  const syncToggles = (theme) => {
    for (const button of document.querySelectorAll("[data-theme-toggle]")) {
      button.setAttribute("aria-pressed", String(theme === "dark"));
    }
  };

  const apply = (theme) => {
    root.setAttribute("data-bs-theme", theme);
    syncToggles(theme);
  };

  apply(stored() || (system.matches ? "dark" : "light"));

  system.addEventListener("change", (event) => {
    if (!stored()) apply(event.matches ? "dark" : "light");
  });

  // Скрипт виконується до появи кнопки, тож клік ловимо делегуванням на document.
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-theme-toggle]")) return;
    const next = root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // Не збереглося — тема діє до перезавантаження сторінки.
    }
    apply(next);
  });

  document.addEventListener("DOMContentLoaded", () => syncToggles(root.getAttribute("data-bs-theme")));
})();
