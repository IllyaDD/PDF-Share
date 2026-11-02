// Пошук (partials/search_form.html): зміна області одразу перезапускає пошук,
// якщо запит уже введено.
document.querySelectorAll('[data-search-scope]').forEach((select) => {
  select.addEventListener('change', () => {
    if (select.form.q.value.trim()) select.form.requestSubmit();
  });
});
