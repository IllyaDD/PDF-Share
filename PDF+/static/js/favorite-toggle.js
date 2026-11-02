// Зірочка «Обране» (partials/favorite_button.html): шле форму через fetch і перемикає
// стан на місці, щоб сторінка не перезавантажувалась і не губила прокрутку. Підписи
// беруться з data-label-on/off форми.
// Сервер відповів не JSON-ом (сесія спливла — редирект на вхід; книга вже недоступна;
// застарілий CSRF) — перезавантажуємо сторінку: повторний POST після входу вів би на GET
// POST-only адреси. Звичайний submit — лише коли запит не дійшов зовсім.
document.addEventListener('submit', async (event) => {
  const form = event.target.closest('[data-favorite-form]');
  if (!form) return;
  event.preventDefault();

  const button = form.querySelector('.favorite-toggle');
  button.disabled = true;
  try {
    let response;
    try {
      response = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
      });
    } catch {
      form.submit();
      return;
    }
    const isJson = (response.headers.get('Content-Type') || '').includes('application/json');
    if (!response.ok || response.redirected || !isJson) {
      window.location.reload();
      return;
    }
    const { favorite } = await response.json();

    const label = favorite ? form.dataset.labelOn : form.dataset.labelOff;
    form.querySelector('input[name="favorite"]').value = favorite ? '0' : '1';
    button.setAttribute('aria-pressed', String(favorite));
    button.title = label;
    button.setAttribute('aria-label', label);
    button.querySelector('i').className = favorite ? 'bi bi-star-fill' : 'bi bi-star';
  } catch {
    window.location.reload();
  } finally {
    button.disabled = false;
  }
});
