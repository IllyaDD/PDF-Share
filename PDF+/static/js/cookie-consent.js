// Банер згоди на кукі (partials/cookie_banner.html).
// Кукі cookie_consent ставить сервер; скрипт шле форму банера через fetch, щоб не
// перезавантажувати сторінку, і відкриває банер знову з кнопки [data-cookie-settings].
// Необов'язкові скрипти (аналітика) перевіряють cookieConsent.allows("analytics") або
// слухають подію "cookieconsent:change" на document.
(() => {
  const read = () => {
    const match = document.cookie.match(/(?:^|;\s*)cookie_consent=(all|necessary)(?:;|$)/);
    return match ? match[1] : null;
  };

  window.cookieConsent = {
    get: read,
    // Обов'язкові дозволені завжди, решта — лише після «Прийняти всі».
    allows: (category) => category === "necessary" || read() === "all",
  };

  const banner = () => document.querySelector("[data-cookie-banner]");

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    const choice = event.submitter?.value;
    if (!form.matches("[data-cookie-form]") || !choice) return;
    event.preventDefault();

    const body = new FormData(form);
    body.set("consent", choice);
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: { "X-Requested-With": "fetch" },
      });
      if (!response.ok) return; // Банер лишається — можна натиснути ще раз.
    } catch {
      return;
    }
    banner().hidden = true;
    document.dispatchEvent(new CustomEvent("cookieconsent:change", { detail: { consent: choice } }));
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-cookie-settings]")) return;
    const element = banner();
    if (!element) return;
    element.hidden = false;
    element.querySelector("button")?.focus();
  });
})();
