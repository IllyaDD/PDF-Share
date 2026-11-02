// Модалка перенесення книги в теку (partials/move_modal.html): тригер передає
// data-action, data-title і поточну теку в data-folder.
(() => {
  const modal = document.getElementById('moveModal');
  if (!modal) return;

  modal.addEventListener('show.bs.modal', (event) => {
    const trigger = event.relatedTarget;
    if (!trigger) return;
    modal.querySelector('[data-move-form]').action = trigger.dataset.action || '';
    modal.querySelector('[data-move-title]').textContent = trigger.dataset.title ? `«${trigger.dataset.title}»` : '';
    modal.querySelector('[data-move-select]').value = trigger.dataset.folder || '';
  });
})();
