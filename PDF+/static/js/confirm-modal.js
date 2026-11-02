// Спільна модалка підтвердження (partials/confirm_modal.html): кнопка-тригер задає
// action форми й тексти через data-action / data-title / data-text / data-confirm.
// Тексти за замовчуванням уже перекладені в розмітці — запам'ятовуємо їх звідти.
(() => {
  const modal = document.getElementById('confirmModal');
  if (!modal) return;

  const form = modal.querySelector('[data-confirm-form]');
  const title = modal.querySelector('[data-confirm-title]');
  const text = modal.querySelector('[data-confirm-text]');
  const submit = modal.querySelector('[data-confirm-submit]');
  const defaults = { title: title.textContent, text: text.textContent, confirm: submit.textContent };

  modal.addEventListener('show.bs.modal', (event) => {
    const trigger = event.relatedTarget;
    if (!trigger) return;
    form.action = trigger.dataset.action || '';
    title.textContent = trigger.dataset.title || defaults.title;
    text.textContent = trigger.dataset.text || defaults.text;
    submit.textContent = trigger.dataset.confirm || defaults.confirm;
  });
})();
