// Форма завантаження PDF: зона перетягування й прогрес відправки через XHR.
// Тексти — через gettext() з каталогу javascript-catalog, його сторінка підключає перед цим файлом.

// Зона перетягування: показує назву й розмір обраного файлу.
(() => {
  const zone = document.querySelector('[data-dropzone]');
  if (!zone) return;

  const input = zone.querySelector('input[type="file"]');
  const nameEl = zone.querySelector('[data-dropzone-name]');
  const sizeEl = zone.querySelector('[data-dropzone-size]');
  const promptEl = zone.querySelector('[data-dropzone-prompt]');

  const formatSize = (bytes) => {
    const units = [gettext('Б'), gettext('КБ'), gettext('МБ'), gettext('ГБ')];
    let value = bytes, unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
    return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
  };

  const showFile = (file) => {
    if (!file) return;
    nameEl.textContent = file.name;
    sizeEl.textContent = formatSize(file.size);
    promptEl.textContent = gettext('Обрати інший файл');
    zone.classList.add('has-file');
  };

  input.addEventListener('change', () => showFile(input.files[0]));

  ['dragenter', 'dragover'].forEach((event) => {
    zone.addEventListener(event, (e) => {
      e.preventDefault();
      zone.classList.add('is-dragover');
    });
  });

  ['dragleave', 'drop'].forEach((event) => {
    zone.addEventListener(event, (e) => {
      e.preventDefault();
      zone.classList.remove('is-dragover');
    });
  });

  zone.addEventListener('drop', (e) => {
    const file = e.dataTransfer.files[0];
    if (!file) return;

    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;

    showFile(file);
  });
})();

// Відправка з прогресом: без XHR-upload форма йде звичайним POST.
(() => {
  const zone = document.querySelector('[data-dropzone]');
  const form = zone && zone.closest('form');
  if (!form) return;

  const submit = form.querySelector('[type="submit"]');
  const box = form.querySelector('[data-upload-progress]');
  const bar = form.querySelector('[data-upload-bar]');
  const status = form.querySelector('[data-upload-status]');
  const input = zone.querySelector('input[type="file"]');
  const label = submit ? submit.value : '';

  if (!submit || !box || !window.FormData || !('upload' in new XMLHttpRequest())) return;

  let busy = false;

  const setBar = (percent, extraClass) => {
    bar.style.width = `${percent}%`;
    bar.className = `progress-bar ${extraClass || ''}`.trim();
    box.querySelector('.progress').setAttribute('aria-valuenow', Math.round(percent));
  };

  const lock = () => {
    busy = true;
    submit.disabled = true;
    submit.value = gettext('Завантаження…');
    input.disabled = true;
    zone.classList.add('is-uploading');
    box.classList.remove('d-none');
    status.className = 'small text-body-secondary mt-2 mb-0';
    status.textContent = gettext('Завантаження…');
    setBar(0);
  };

  const unlock = (message) => {
    busy = false;
    submit.disabled = false;
    submit.value = label;
    input.disabled = false;
    zone.classList.remove('is-uploading');
    setBar(100, 'bg-danger');
    status.className = 'small text-danger mt-2 mb-0';
    status.textContent = message;
  };

  form.addEventListener('submit', (event) => {
    event.preventDefault();

    if (busy) return;

    const data = new FormData(form);
    const action = form.action || window.location.href;
    lock();

    const xhr = new XMLHttpRequest();
    xhr.open('POST', action);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');

    xhr.upload.addEventListener('progress', (e) => {
      if (!e.lengthComputable) return;
      const percent = (e.loaded / e.total) * 100;
      setBar(percent);
      status.textContent = `${gettext('Завантаження…')} ${Math.round(percent)}%`;
    });

    xhr.upload.addEventListener('load', () => {
      setBar(100, 'progress-bar-striped progress-bar-animated');
      status.textContent = gettext('Обробка файлу…');
    });

    xhr.addEventListener('load', () => {
      if (xhr.status >= 400) {
        unlock(gettext('Сервер відхилив завантаження. Спробуйте ще раз.'));
        return;
      }

      if (xhr.responseURL && xhr.responseURL.split('#')[0] !== action.split('#')[0]) {
        window.location.assign(xhr.responseURL);
        return;
      }

      document.open();
      document.write(xhr.responseText);
      document.close();
    });

    ['error', 'abort', 'timeout'].forEach((name) => {
      xhr.addEventListener(name, () => unlock(gettext("Не вдалося завантажити файл. Перевірте зв'язок і спробуйте ще раз.")));
    });

    xhr.send(data);
  });
})();
