// Керування читалкою: пагінація, масштаб, перемикач панелі нотаток.
// Класичний скрипт: виконується раніше за модуль reader.js і лише шле йому події
// reader:page / reader:scale, а від нього слухає reader:current.
(() => {
  const viewer = document.getElementById('pdf-viewer');
  const panel = document.getElementById('readerPanel');
  const pageInput = document.querySelector('[data-reader-page]');
  const prevButton = document.querySelector('[data-reader-prev]');
  const nextButton = document.querySelector('[data-reader-next]');
  const firstButton = document.querySelector('[data-reader-first]');
  const lastButton = document.querySelector('[data-reader-last]');
  const zoomLabel = document.querySelector('[data-reader-zoom-label]');
  const panelToggle = document.querySelector('[data-reader-toggle-panel]');
  const pageCount = Number(viewer.dataset.pageCount) || 1;
  let page = 1;
  let scale = 1;

  // Лише відображення: поле, межові кнопки й data-атрибут, без прокрутки.
  const showPage = (value) => {
    page = value;
    viewer.dataset.currentPage = page;
    prevButton.disabled = firstButton.disabled = page <= 1;
    nextButton.disabled = lastButton.disabled = page >= pageCount;
    if (document.activeElement !== pageInput) {
      pageInput.value = page;
    }
  };

  const setPage = (value) => {
    showPage(Math.min(Math.max(value, 1), pageCount));
    pageInput.value = page;
    viewer.dispatchEvent(new CustomEvent('reader:page', { detail: { page } }));
  };

  // Порожнє чи нечислове введення повертає поточну сторінку замість стрибка на 1.
  const commitInput = () => {
    const value = Number.parseInt(pageInput.value, 10);
    if (Number.isNaN(value)) {
      pageInput.value = page;
    } else {
      setPage(value);
    }
  };

  const setScale = (value) => {
    scale = Math.min(Math.max(value, 0.5), 3);
    viewer.dataset.scale = scale.toFixed(2);
    zoomLabel.textContent = `${Math.round(scale * 100)} %`;
    viewer.dispatchEvent(new CustomEvent('reader:scale', { detail: { scale } }));
  };

  prevButton.addEventListener('click', () => setPage(page - 1));
  nextButton.addEventListener('click', () => setPage(page + 1));
  firstButton.addEventListener('click', () => setPage(1));
  lastButton.addEventListener('click', () => setPage(pageCount));

  pageInput.addEventListener('focus', () => pageInput.select());
  pageInput.addEventListener('input', () => {
    pageInput.value = pageInput.value.replace(/\D/g, '');
  });
  pageInput.addEventListener('change', commitInput);
  // change спрацьовує раніше за blur, тож тут поле наздоганяє прокрутку під час фокусу.
  pageInput.addEventListener('blur', () => {
    pageInput.value = page;
  });
  pageInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      commitInput();
      pageInput.select();
    } else if (event.key === 'Escape') {
      pageInput.value = page;
      pageInput.blur();
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      event.preventDefault();
      setPage(page + (event.key === 'ArrowUp' ? 1 : -1));
      pageInput.select();
    }
  });
  document.querySelector('[data-reader-zoom-in]').addEventListener('click', () => setScale(scale + 0.25));
  document.querySelector('[data-reader-zoom-out]').addEventListener('click', () => setScale(scale - 0.25));
  // Панель видно за замовчуванням лише від lg: там її ховає is-collapsed, нижче — показує is-open.
  const wide = window.matchMedia('(min-width: 992px)');
  const syncToggle = () => {
    const shown = wide.matches ? !panel.classList.contains('is-collapsed') : panel.classList.contains('is-open');
    panelToggle.classList.toggle('active', shown);
    panelToggle.setAttribute('aria-pressed', String(shown));
  };
  panelToggle.addEventListener('click', () => {
    panel.classList.toggle(wide.matches ? 'is-collapsed' : 'is-open');
    syncToggle();
  });
  wide.addEventListener('change', syncToggle);
  syncToggle();
  // Поки користувач набирає номер, прокрутка не перезаписує поле.
  viewer.addEventListener('reader:current', (event) => showPage(event.detail.page));

  setPage(Number(new URLSearchParams(location.search).get('page')) || 1);
  setScale(1);
})();
