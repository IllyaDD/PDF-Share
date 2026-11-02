// Кнопка [data-copy-input] копіює значення поля зі свого .input-group;
// якщо буфер обміну недоступний — виділяє текст, щоб скопіювати вручну.
document.querySelectorAll('[data-copy-input]').forEach((button) => {
  button.addEventListener('click', async () => {
    const input = button.closest('.input-group').querySelector('input');
    try {
      await navigator.clipboard.writeText(input.value);
    } catch {
      input.select();
      return;
    }
    const icon = button.querySelector('i');
    icon.className = 'bi bi-check-lg';
    setTimeout(() => { icon.className = 'bi bi-clipboard'; }, 1500);
  });
});
