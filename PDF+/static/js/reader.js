// Переклади дає JavaScriptCatalog, який шаблон підключає перед модулем;
// без нього рядки лишаються українськими msgid.
const gettext = window.gettext ?? ((text) => text);
const interpolate = window.interpolate
  ?? ((format, values) => format.replace(/%\((\w+)\)s/g, (_, key) => values[key]));

const viewer = document.getElementById("pdf-viewer");

if (viewer) {
  const pdfjsLib = await import(viewer.dataset.pdfjsUrl);
  pdfjsLib.GlobalWorkerOptions.workerSrc = viewer.dataset.pdfjsWorkerUrl;

  const MIN_SCALE = 0.5;
  const MAX_SCALE = 3;
  // Сторінка вважається поточною, щойно її верх підійшов до верху в'юера ближче за цей відступ.
  const PAGE_ANCHOR_OFFSET = 40;
  const PROGRESS_SAVE_DELAY = 1500;

  const canAnnotate = viewer.dataset.canAnnotate === "1";
  const csrfToken = viewer.dataset.csrfToken;
  const progressUrl = viewer.dataset.progressUrl;
  const palette = document.getElementById("highlightPalette");
  const popover = document.getElementById("annotationPopover");
  const sidebar = document.getElementById("annotationList");
  const counter = document.querySelector("[data-reader-toggle-panel] .badge");
  const filterBar = document.getElementById("annotationFilter");
  const filterChips = filterBar?.querySelector("[data-filter-chips]");
  const filterSummary = filterBar?.querySelector("[data-filter-summary]");
  const filterReset = filterBar?.querySelector("[data-filter-reset]");
  const layerSwitch = document.getElementById("layerSwitch");
  const layerTitle = document.querySelector("[data-layer-title]");
  const topicSwitch = document.getElementById("topicSwitch");
  const topicChips = topicSwitch?.querySelector("[data-topic-chips]");
  const topicAdd = topicSwitch?.querySelector("[data-topic-add]");
  const topicForm = topicSwitch?.querySelector("[data-topic-form]");
  const topicHint = topicSwitch?.querySelector("[data-topic-hint]");
  const topicError = topicSwitch?.querySelector("[data-topic-error]");
  const popoverTopicChips = popover?.querySelector("[data-popover-topic-chips]");
  const popoverTopicForm = popover?.querySelector("[data-popover-topic-form]");
  const TOPIC_STORAGE_KEY = `pdfplus:topic:${viewer.dataset.bookId}`;

  // Власний шар: лише його змінюють палітра, попап і запити до API.
  const annotations = new Map();
  // Шари вчителів — з публікації для групи читача або з посилання «з нотатками»: лише для читання.
  const sharedLayers = new Map();
  const pageViewports = new Map();
  const pageDivs = new Map();

  const hiddenColors = new Set();
  const chipNodes = new Map();

  // Теми власного шару. activeTopic — "all" | "none" | id теми рядком:
  // і куди йдуть нові виділення, і що показується.
  const noteGroups = new Map();
  let activeTopic = "all";

  let pdf = null;
  let scale = 1;
  let pendingSelection = null;
  let activeId = null;
  let navId = null;
  let flashTimer = null;

  // До стартового позиціювання й під час reflow в'юер стоїть на верху документа —
  // без цього прапорця трекер записав би с. 1 поверх збереженого прогресу.
  let tracking = false;
  let currentPage = 0;
  let savedPage = Number(viewer.dataset.lastPage) || 0;
  let saveTimer = null;
  let trackFrame = 0;

  const dataNode = document.getElementById("annotations-data");
  if (dataNode) {
    for (const item of JSON.parse(dataNode.textContent)) {
      annotations.set(item.id, item);
    }
  }

  const sharedNode = document.getElementById("shared-layers-data");
  if (sharedNode) {
    for (const layer of JSON.parse(sharedNode.textContent)) {
      sharedLayers.set(layer.id, {
        id: layer.id,
        owner: layer.owner,
        annotations: new Map(layer.annotations.map((item) => [item.id, item])),
      });
    }
  }

  const groupsNode = document.getElementById("note-groups-data");
  if (groupsNode) {
    for (const group of JSON.parse(groupsNode.textContent)) {
      noteGroups.set(group.id, group);
    }
  }

  // Шар, що зараз на сторінках, у панелі та у фільтрах.
  let shown = annotations;
  const ownShown = () => shown === annotations;

  const paletteLabels = new Map();
  const paletteNode = document.getElementById("palette-data");
  if (paletteNode) {
    for (const [value, label] of JSON.parse(paletteNode.textContent)) {
      paletteLabels.set(value, label);
    }
  }

  // Теми — лише у власному шарі; на чужих шарах фільтр тем не діє.
  function matchesTopic(annotation) {
    if (!ownShown() || activeTopic === "all") {
      return true;
    }
    if (activeTopic === "none") {
      return annotation.group === null;
    }
    return annotation.group === Number(activeTopic);
  }

  const isVisible = (annotation) =>
    !hiddenColors.has(annotation.color) && matchesTopic(annotation);

  const topicScoped = () => [...shown.values()].filter(matchesTopic);

  const byPageThenId = (a, b) => a.page - b.page || a.id - b.id;

  function visibleSorted() {
    return [...shown.values()].filter(isVisible).sort(byPageThenId);
  }

  async function request(url, method, body, { keepalive = false } = {}) {
    const response = await fetch(url, {
      method,
      credentials: "same-origin",
      keepalive,
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      body: body === undefined ? undefined : JSON.stringify(body),
    });

    if (response.status === 204) {
      return null;
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  const detailUrl = (id) =>
    viewer.dataset.annotationUrlTemplate.replace("/0/", `/${id}/`);

  function toViewportRect(coords, viewport) {
    const [ax, ay] = viewport.convertToViewportPoint(coords.x0, coords.y0);
    const [bx, by] = viewport.convertToViewportPoint(coords.x1, coords.y1);
    return {
      left: Math.min(ax, bx),
      top: Math.min(ay, by),
      width: Math.abs(bx - ax),
      height: Math.abs(by - ay),
    };
  }

  function toPdfRect(rect, canvasRect, viewport) {
    const [ax, ay] = viewport.convertToPdfPoint(
      rect.left - canvasRect.left,
      rect.top - canvasRect.top,
    );
    const [bx, by] = viewport.convertToPdfPoint(
      rect.right - canvasRect.left,
      rect.bottom - canvasRect.top,
    );
    return {
      x0: Math.min(ax, bx),
      y0: Math.min(ay, by),
      x1: Math.max(ax, bx),
      y1: Math.max(ay, by),
    };
  }

  function drawPage(pageNumber) {
    const pageDiv = pageDivs.get(pageNumber);
    const layer = pageDiv?.querySelector(".annotation-layer");
    const viewport = pageViewports.get(pageNumber);
    if (!layer || !viewport) {
      return;
    }

    layer.replaceChildren();
    const readonly = !ownShown();

    for (const annotation of shown.values()) {
      if (annotation.page !== pageNumber || !isVisible(annotation)) {
        continue;
      }

      for (const coords of annotation.coords || []) {
        const rect = toViewportRect(coords, viewport);
        const mark = document.createElement("div");
        mark.className = readonly ? "annotation-rect is-readonly" : "annotation-rect";
        mark.dataset.annotationId = String(annotation.id);
        mark.style.left = `${rect.left}px`;
        mark.style.top = `${rect.top}px`;
        mark.style.width = `${rect.width}px`;
        mark.style.height = `${rect.height}px`;
        mark.style.backgroundColor = annotation.color;
        if (annotation.note) {
          mark.title = annotation.note;
        }
        layer.append(mark);
      }
    }
  }

  function redrawAll() {
    for (const pageNumber of pageViewports.keys()) {
      drawPage(pageNumber);
    }
  }

  function buildSidebarItem(annotation) {
    const card = document.createElement("div");
    card.className = "card border-0 shadow-sm mb-2";
    card.dataset.annotationItem = String(annotation.id);

    const body = document.createElement("div");
    body.className = "card-body p-3";

    const head = document.createElement("div");
    head.className = "d-flex align-items-center gap-2 mb-2";

    const dot = document.createElement("span");
    dot.className = "rounded-circle flex-shrink-0";
    dot.style.cssText = `width:.75rem;height:.75rem;background:${annotation.color};`;

    const badge = document.createElement("span");
    badge.className = "badge text-bg-light border";
    badge.textContent = interpolate(gettext("с. %(page)s"), { page: annotation.page }, true);

    head.append(dot, badge);
    body.append(head);

    if (annotation.text) {
      const quote = document.createElement("blockquote");
      quote.className = "border-start border-3 ps-2 mb-2 small fst-italic text-body-secondary";
      quote.style.borderColor = `${annotation.color} !important`;
      quote.textContent = annotation.text.slice(0, 220);
      body.append(quote);
    }

    if (annotation.note) {
      const note = document.createElement("p");
      note.className = "small mb-0";
      note.textContent = annotation.note;
      body.append(note);
    }

    card.append(body);
    card.addEventListener("click", () => focusAnnotation(annotation.id));
    return card;
  }

  function renderSidebar() {
    const sorted = visibleSorted();

    const total = topicScoped().length;
    if (counter) {
      counter.textContent = hiddenColors.size ? `${sorted.length}/${total}` : String(total);
    }
    if (!sidebar) {
      return;
    }

    if (!sorted.length) {
      const topicFiltered = ownShown() && activeTopic !== "all";
      if (sidebar.dataset.jsOwned !== "1" && !hiddenColors.size && !topicFiltered) {
        return;
      }

      sidebar.dataset.jsOwned = "1";
      const empty = document.createElement("p");
      empty.className = "text-body-secondary small text-center py-4 mb-0";
      if (hiddenColors.size) {
        empty.textContent = gettext("Усі мітки сховані фільтром.");
      } else if (topicFiltered) {
        empty.textContent = gettext("У цій темі нотаток ще немає.");
      } else {
        empty.textContent = ownShown() ? gettext("Нотаток ще немає.") : gettext("У цьому шарі нотаток немає.");
      }
      sidebar.replaceChildren(empty);
      return;
    }

    sidebar.dataset.jsOwned = "1";
    sidebar.replaceChildren(...sorted.map(buildSidebarItem));
  }

  function buildFilterChip(color) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "filter-chip";
    chip.dataset.filterColor = color;
    chip.title = gettext("Клік — сховати/показати. Подвійний клік — лише цей колір.");

    const dot = document.createElement("span");
    dot.className = "filter-chip-dot";
    dot.style.background = color;

    const text = document.createElement("span");
    text.className = "filter-chip-label";
    text.textContent = paletteLabels.get(color) || color;

    const badge = document.createElement("span");
    badge.className = "text-body-secondary";
    badge.dataset.chipCount = "";

    chip.append(dot, text, badge);
    return chip;
  }

  function syncFilterChip(chip, color, count) {
    const hidden = hiddenColors.has(color);
    chip.classList.toggle("is-off", hidden);
    chip.setAttribute("aria-pressed", String(!hidden));
    chip.setAttribute("aria-label", `${paletteLabels.get(color) || color}: ${count}`);
    chip.querySelector("[data-chip-count]").textContent = String(count);
  }

  function colorCounts() {
    const counts = new Map();
    for (const annotation of topicScoped()) {
      counts.set(annotation.color, (counts.get(annotation.color) || 0) + 1);
    }
    return counts;
  }

  function pruneHiddenColors(counts) {

    if (counts.size < 2) {
      hiddenColors.clear();
      return;
    }
    for (const color of hiddenColors) {
      if (!counts.has(color)) {
        hiddenColors.delete(color);
      }
    }
  }

  function renderFilters(counts) {
    if (!filterBar || !filterChips) {
      return;
    }

    filterBar.classList.toggle("d-none", counts.size < 2);
    if (counts.size < 2) {
      return;
    }

    const known = [...paletteLabels.keys()];
    const rank = (color) => {
      const index = known.indexOf(color);
      return index < 0 ? known.length : index;
    };
    const ordered = [...counts.keys()].sort(
      (a, b) => rank(a) - rank(b) || a.localeCompare(b),
    );

    const sameSet =
      chipNodes.size === ordered.length && ordered.every((color) => chipNodes.has(color));
    if (!sameSet) {
      chipNodes.clear();
      for (const color of ordered) {
        chipNodes.set(color, buildFilterChip(color));
      }
      filterChips.replaceChildren(...chipNodes.values());
    }
    for (const [color, chip] of chipNodes) {
      syncFilterChip(chip, color, counts.get(color));
    }

    if (filterSummary) {
      const total = topicScoped().length;
      filterSummary.textContent = hiddenColors.size
        ? interpolate(gettext("%(shown)s з %(total)s"), { shown: visibleSorted().length, total }, true)
        : interpolate(gettext("%(total)s міток"), { total }, true);
    }
    filterReset?.classList.toggle("d-none", hiddenColors.size === 0);
  }

  function refresh() {
    const counts = colorCounts();
    pruneHiddenColors(counts);

    const active = activeId === null ? null : annotations.get(activeId);
    if (active && !isVisible(active)) {
      hidePopover();
    }

    redrawAll();
    renderSidebar();
    renderFilters(counts);
  }

  function sortedGroups() {
    return [...noteGroups.values()].sort((a, b) => a.name.localeCompare(b.name, "uk"));
  }

  function buildTopicChip(value, label, active) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `btn btn-sm rounded-pill topic-chip ${active ? "btn-primary" : "btn-outline-secondary"}`;
    chip.dataset.topic = value;
    chip.setAttribute("aria-pressed", String(active));
    chip.textContent = label;
    return chip;
  }

  function renderTopics() {
    if (topicChips) {
      topicChips.replaceChildren(
        buildTopicChip("all", gettext("Усі"), activeTopic === "all"),
        buildTopicChip("none", gettext("Без групи"), activeTopic === "none"),
        ...sortedGroups().map((group) =>
          buildTopicChip(String(group.id), group.name, activeTopic === String(group.id)),
        ),
      );
    }
    if (topicHint) {
      const group = noteGroups.get(Number(activeTopic));
      topicHint.textContent = group
        ? interpolate(gettext("Нові виділення потрапляють у тему «%(name)s»."), { name: group.name }, true)
        : gettext("Нові виділення потрапляють у «Без групи».");
    }
  }

  // Чипи тем у поповері: поточна тема нотатки + «+ Нова» з інлайн-полем.
  function renderPopoverTopics(annotation) {
    if (!popoverTopicChips || !annotation) {
      return;
    }
    const current = annotation.group === null ? "none" : String(annotation.group);
    const add = buildTopicChip("new", gettext("+ Нова"), false);
    add.classList.replace("btn-outline-secondary", "btn-outline-primary");
    popoverTopicChips.replaceChildren(
      buildTopicChip("none", gettext("Без групи"), current === "none"),
      ...sortedGroups().map((group) =>
        buildTopicChip(String(group.id), group.name, current === String(group.id)),
      ),
      add,
    );
  }

  function hidePopoverTopicForm() {
    if (popoverTopicForm) {
      popoverTopicForm.classList.add("d-none");
      popoverTopicForm.querySelector("[data-popover-topic-name]").value = "";
    }
  }

  function setTopic(key) {
    activeTopic = key === "none" || noteGroups.has(Number(key)) ? String(key) : "all";
    try {
      localStorage.setItem(TOPIC_STORAGE_KEY, activeTopic);
    } catch {
      // Сховище недоступне (приватний режим) — тема просто не запам'ятається.
    }
    navId = null;
    renderTopics();
    refresh();
  }

  const newHighlightGroup = () =>
    noteGroups.has(Number(activeTopic)) ? Number(activeTopic) : null;

  async function createTopic(name) {
    const group = await request(viewer.dataset.noteGroupsUrl, "POST", { name });
    noteGroups.set(group.id, group);
    return group;
  }

  function showLayer(key) {
    const layer = key === "own" ? null : sharedLayers.get(Number(key));
    if (key !== "own" && !layer) {
      return;
    }

    shown = layer ? layer.annotations : annotations;
    hiddenColors.clear();
    navId = null;
    hidePopover();
    if (sidebar) {
      sidebar.dataset.jsOwned = "1";
    }

    const activeKey = layer ? String(layer.id) : "own";
    layerSwitch?.querySelectorAll("[data-layer]").forEach((button) => {
      const active = button.dataset.layer === activeKey;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    layerSwitch?.querySelector("[data-layer-readonly]")?.classList.toggle("d-none", !layer);
    document
      .querySelectorAll("[data-own-only]")
      .forEach((node) => node.classList.toggle("d-none", Boolean(layer)));
    if (layerTitle) {
      layerTitle.textContent = layer ? interpolate(gettext("Нотатки: %(owner)s"), { owner: layer.owner }, true) : gettext("Мої нотатки");
    }

    refresh();
  }

  async function focusAnnotation(id, behavior = "smooth") {
    const annotation = shown.get(id);
    if (!annotation) {
      return;
    }
    navId = id;

    if (hiddenColors.delete(annotation.color)) {
      refresh();
    }

    await renderPage(annotation.page);

    const mark = document.querySelector(`.annotation-rect[data-annotation-id="${id}"]`);

    (mark ?? pageDivs.get(annotation.page))?.scrollIntoView({ behavior, block: "center" });

    clearTimeout(flashTimer);
    document
      .querySelectorAll(".annotation-rect.is-target")
      .forEach((node) => node.classList.remove("is-target"));

    if (mark) {
      void mark.offsetWidth;
      mark.classList.add("is-target");
      flashTimer = setTimeout(() => mark.classList.remove("is-target"), 1600);
    }
  }

  function stepAnnotation(delta) {
    const sorted = visibleSorted();
    if (!sorted.length) {
      return;
    }

    const current = sorted.findIndex((item) => item.id === navId);
    let next = delta > 0 ? 0 : sorted.length - 1;
    if (current >= 0) {
      next = Math.min(Math.max(current + delta, 0), sorted.length - 1);
    }
    focusAnnotation(sorted[next].id);
  }

  function place(panel, x, y) {
    panel.hidden = false;
    const rect = panel.getBoundingClientRect();
    const left = Math.min(Math.max(x - rect.width / 2, 8), window.innerWidth - rect.width - 8);
    const top = Math.max(y - rect.height - 10, 8);
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  }

  function hidePalette() {
    if (palette) {
      palette.hidden = true;
    }
    pendingSelection = null;
  }

  function hidePopover() {
    if (popover) {
      popover.hidden = true;
    }
    document
      .querySelectorAll(".annotation-rect.is-focused")
      .forEach((node) => node.classList.remove("is-focused"));
    activeId = null;
  }

  function readSelection() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) {
      return null;
    }

    const range = selection.getRangeAt(0);
    const startPage = range.startContainer.parentElement?.closest(".pdf-page");
    const endPage = range.endContainer.parentElement?.closest(".pdf-page");

    if (!startPage || startPage !== endPage) {
      return null;
    }

    const pageNumber = Number(startPage.dataset.pageNumber);
    const viewport = pageViewports.get(pageNumber);
    const canvas = startPage.querySelector("canvas");
    if (!viewport || !canvas) {
      return null;
    }

    const canvasRect = canvas.getBoundingClientRect();
    const coords = [...range.getClientRects()]
      .filter((rect) => rect.width > 0.5 && rect.height > 0.5)
      .map((rect) => toPdfRect(rect, canvasRect, viewport));

    if (!coords.length) {
      return null;
    }

    const last = range.getClientRects()[range.getClientRects().length - 1];
    return {
      page_number: pageNumber,
      coords,
      selected_text: selection.toString(),
      anchor: { x: last.left + last.width / 2, y: last.top },
    };
  }

  async function createHighlight(color) {
    if (!pendingSelection) {
      return;
    }
    const { anchor, ...payload } = pendingSelection;

    try {
      const created = await request(viewer.dataset.annotationsUrl, "POST", {
        ...payload,
        color,
        group: newHighlightGroup(),
      });
      annotations.set(created.id, created);

      if (ownShown()) {
        hiddenColors.delete(created.color);
        refresh();
      } else {
        // Нове виділення завжди потрапляє у власний шар — показуємо саме його.
        showLayer("own");
      }
      window.getSelection()?.removeAllRanges();
    } catch (error) {
      alert(interpolate(gettext("Не вдалося зберегти виділення: %(error)s"), { error: error.message }, true));
    } finally {
      hidePalette();
    }
  }

  function openPopover(id, x, y) {
    const annotation = annotations.get(id);
    if (!annotation || !popover) {
      return;
    }

    activeId = id;
    popover.querySelector("[data-note]").value = annotation.note || "";
    popover.querySelector("[data-status]").textContent = "";
    hidePopoverTopicForm();
    renderPopoverTopics(annotation);
    popover.querySelectorAll(".swatch").forEach((swatch) => {
      swatch.classList.toggle("is-active", swatch.dataset.color === annotation.color);
    });
    document
      .querySelectorAll(`.annotation-rect[data-annotation-id="${id}"]`)
      .forEach((node) => node.classList.add("is-focused"));

    place(popover, x, y);
  }

  async function patchActive(fields, status) {
    if (activeId === null) {
      return;
    }
    const statusEl = popover.querySelector("[data-status]");
    try {
      const updated = await request(detailUrl(activeId), "PATCH", fields);
      annotations.set(updated.id, updated);

      hiddenColors.delete(updated.color);
      refresh();
      document
        .querySelectorAll(`.annotation-rect[data-annotation-id="${updated.id}"]`)
        .forEach((node) => node.classList.add("is-focused"));
      statusEl.textContent = status;
    } catch (error) {
      statusEl.textContent = error.message;
    }
  }

  async function deleteActive() {
    if (activeId === null) {
      return;
    }
    try {
      await request(detailUrl(activeId), "DELETE");
      annotations.delete(activeId);
      hidePopover();
      refresh();
    } catch (error) {
      popover.querySelector("[data-status]").textContent = error.message;
    }
  }

  async function renderPage(pageNumber) {
    const pageDiv = pageDivs.get(pageNumber);
    if (!pageDiv || pageDiv.dataset.rendered === String(scale)) {
      return;
    }
    pageDiv.dataset.rendered = String(scale);

    const page = await pdf.getPage(pageNumber);
    const viewport = page.getViewport({ scale });
    pageViewports.set(pageNumber, viewport);

    pageDiv.style.width = `${Math.floor(viewport.width)}px`;
    pageDiv.style.height = `${Math.floor(viewport.height)}px`;

    pageDiv.style.setProperty("--total-scale-factor", String(scale));

    const outputScale = window.devicePixelRatio || 1;
    const canvas = document.createElement("canvas");
    canvas.width = Math.floor(viewport.width * outputScale);
    canvas.height = Math.floor(viewport.height * outputScale);
    canvas.style.width = `${Math.floor(viewport.width)}px`;
    canvas.style.height = `${Math.floor(viewport.height)}px`;

    await page.render({
      canvas,
      viewport,
      transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : null,
    }).promise;

    const textLayerDiv = document.createElement("div");
    textLayerDiv.className = "textLayer";
    pdfjsLib.setLayerDimensions(textLayerDiv, viewport);

    await new pdfjsLib.TextLayer({
      textContentSource: page.streamTextContent(),
      container: textLayerDiv,
      viewport,
    }).render();

    const annotationLayer = document.createElement("div");
    annotationLayer.className = "annotation-layer";

    pageDiv.replaceChildren(canvas, annotationLayer, textLayerDiv);
    drawPage(pageNumber);
  }

  function pageAtTop() {
    const total = pageDivs.size;
    if (!total) {
      return 0;
    }
    if (
      viewer.scrollTop > 0 &&
      viewer.scrollTop + viewer.clientHeight >= viewer.scrollHeight - 2
    ) {
      return total;
    }

    const anchor = viewer.getBoundingClientRect().top + PAGE_ANCHOR_OFFSET;
    let low = 1;
    let high = total;
    let found = 1;
    while (low <= high) {
      const middle = (low + high) >> 1;
      if (pageDivs.get(middle).getBoundingClientRect().top <= anchor) {
        found = middle;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    return found;
  }

  function saveProgress({ keepalive = false } = {}) {
    clearTimeout(saveTimer);
    const page = currentPage;
    if (!progressUrl || !page || page === savedPage) {
      return;
    }
    savedPage = page;
    request(progressUrl, "POST", { page }, { keepalive }).catch(() => {
      // Наступна зміна сторінки чи закриття вкладки спробує ще раз.
      if (savedPage === page) {
        savedPage = 0;
      }
    });
  }

  function trackCurrentPage() {
    if (!tracking) {
      return;
    }
    const page = pageAtTop();
    if (!page || page === currentPage) {
      return;
    }
    currentPage = page;
    viewer.dispatchEvent(new CustomEvent("reader:current", { detail: { page } }));

    if (progressUrl) {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(saveProgress, PROGRESS_SAVE_DELAY);
    }
  }

  async function goToPage(pageNumber) {
    await renderPage(pageNumber);
    pageDivs.get(pageNumber)?.scrollIntoView({ block: "start" });
  }

  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          renderPage(Number(entry.target.dataset.pageNumber));
        }
      }
    },
    { root: viewer, rootMargin: "200% 0px" },
  );

  async function reflow() {
    const first = await pdf.getPage(1);
    const estimate = first.getViewport({ scale });

    viewer.replaceChildren();
    pageDivs.clear();
    pageViewports.clear();

    for (let number = 1; number <= pdf.numPages; number += 1) {
      const pageDiv = document.createElement("div");
      pageDiv.className = "pdf-page";
      pageDiv.dataset.pageNumber = String(number);
      pageDiv.style.width = `${Math.floor(estimate.width)}px`;
      pageDiv.style.height = `${Math.floor(estimate.height)}px`;

      viewer.append(pageDiv);
      pageDivs.set(number, pageDiv);
      observer.observe(pageDiv);
    }
  }

  viewer.addEventListener(
    "scroll",
    () => {
      if (!trackFrame) {
        trackFrame = requestAnimationFrame(() => {
          trackFrame = 0;
          trackCurrentPage();
        });
      }
    },
    { passive: true },
  );

  if (progressUrl) {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") {
        saveProgress({ keepalive: true });
      }
    });
    window.addEventListener("pagehide", () => saveProgress({ keepalive: true }));
  }

  // Без smooth: плавний проліт через сотню сторінок рендерить кожну дорогою, а
  // проміжні reader:current збивають лічильник при швидких кліках.
  viewer.addEventListener("reader:page", (event) => goToPage(event.detail.page));

  filterChips?.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-filter-color]");
    if (!chip) {
      return;
    }
    const color = chip.dataset.filterColor;
    if (hiddenColors.has(color)) {
      hiddenColors.delete(color);
    } else {
      hiddenColors.add(color);
    }
    refresh();
  });

  filterChips?.addEventListener("dblclick", (event) => {
    const chip = event.target.closest("[data-filter-color]");
    if (!chip) {
      return;
    }
    hiddenColors.clear();
    for (const annotation of shown.values()) {
      if (annotation.color !== chip.dataset.filterColor) {
        hiddenColors.add(annotation.color);
      }
    }
    refresh();
  });

  layerSwitch?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-layer]");
    if (button) {
      showLayer(button.dataset.layer);
    }
  });

  filterReset?.addEventListener("click", () => {
    hiddenColors.clear();
    refresh();
  });

  filterBar
    ?.querySelector("[data-annotation-prev]")
    ?.addEventListener("click", () => stepAnnotation(-1));
  filterBar
    ?.querySelector("[data-annotation-next]")
    ?.addEventListener("click", () => stepAnnotation(1));

  viewer.addEventListener("reader:scale", async (event) => {
    const next = Math.min(Math.max(event.detail.scale, MIN_SCALE), MAX_SCALE);
    if (next === scale) {
      return;
    }
    scale = next;
    hidePalette();
    hidePopover();

    const keepPage = currentPage;
    tracking = false;
    await reflow();
    if (keepPage) {
      await goToPage(keepPage);
    }
    tracking = true;
    trackCurrentPage();
  });

  if (canAnnotate) {
    document.addEventListener("mouseup", (event) => {
      if (palette?.contains(event.target) || popover?.contains(event.target)) {
        return;
      }
      const selection = readSelection();

      if (!selection) {
        const mark = document
          .elementsFromPoint(event.clientX, event.clientY)
          .find((node) => node.classList?.contains("annotation-rect"));

        hidePalette();
        // Чужі мітки без попапу редагування: коментар видно в підказці й у панелі.
        if (mark && ownShown()) {
          openPopover(Number(mark.dataset.annotationId), event.clientX, event.clientY);
        } else {
          hidePopover();
        }
        return;
      }

      hidePopover();
      pendingSelection = selection;
      place(palette, selection.anchor.x, selection.anchor.y);
    });

    palette?.addEventListener("click", (event) => {
      const swatch = event.target.closest(".swatch");
      if (swatch) {
        createHighlight(swatch.dataset.color);
      }
    });

    popover?.addEventListener("click", (event) => {
      const swatch = event.target.closest(".swatch");
      if (swatch) {
        popover.querySelectorAll(".swatch").forEach((node) =>
          node.classList.toggle("is-active", node === swatch),
        );
        patchActive({ color: swatch.dataset.color }, gettext("Колір змінено"));
        return;
      }
      if (event.target.closest("[data-delete]")) {
        deleteActive();
        return;
      }
      if (event.target.closest("[data-close]")) {
        hidePopover();
      }
    });

    popover?.querySelector("[data-note]")?.addEventListener("blur", (event) => {
      const annotation = annotations.get(activeId);
      if (annotation && event.target.value !== (annotation.note || "")) {
        patchActive({ note_text: event.target.value }, gettext("Збережено"));
      }
    });

    topicChips?.addEventListener("click", (event) => {
      const chip = event.target.closest("[data-topic]");
      if (chip) {
        setTopic(chip.dataset.topic);
      }
    });

    topicAdd?.addEventListener("click", () => {
      topicForm?.classList.remove("d-none");
      topicForm?.querySelector("[data-topic-name]")?.focus();
    });

    topicForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = topicForm.querySelector('[type="submit"]');
      // Подвійний Enter інакше шле два POST: другий отримає «тема вже є».
      if (submit.disabled) {
        return;
      }
      submit.disabled = true;
      const input = topicForm.querySelector("[data-topic-name]");
      try {
        const group = await createTopic(input.value);
        input.value = "";
        topicError.textContent = "";
        topicForm.classList.add("d-none");
        setTopic(String(group.id));
      } catch (error) {
        topicError.textContent = error.message;
      } finally {
        submit.disabled = false;
      }
    });

    topicForm?.querySelector("[data-topic-cancel]")?.addEventListener("click", () => {
      topicForm.classList.add("d-none");
      topicError.textContent = "";
    });

    async function moveActiveTo(groupId) {
      await patchActive({ group: groupId }, gettext("Тему змінено"));
      // Нотатка, що вийшла з показаної теми, зникає, а поповер закривається в refresh().
      renderPopoverTopics(annotations.get(activeId));
    }

    popoverTopicChips?.addEventListener("click", (event) => {
      const chip = event.target.closest("[data-topic]");
      if (!chip) {
        return;
      }
      if (chip.dataset.topic === "new") {
        popoverTopicForm?.classList.remove("d-none");
        popoverTopicForm?.querySelector("[data-popover-topic-name]")?.focus();
        return;
      }
      moveActiveTo(chip.dataset.topic === "none" ? null : Number(chip.dataset.topic));
    });

    popoverTopicForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = popoverTopicForm.querySelector('[type="submit"]');
      if (submit.disabled) {
        return;
      }
      submit.disabled = true;
      const input = popoverTopicForm.querySelector("[data-popover-topic-name]");
      try {
        const group = await createTopic(input.value);
        hidePopoverTopicForm();
        renderTopics();
        await moveActiveTo(group.id);
      } catch (error) {
        popover.querySelector("[data-status]").textContent = error.message;
      } finally {
        submit.disabled = false;
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        hidePalette();
        hidePopover();
      }
    });
  }

  let storedTopic = null;
  try {
    storedTopic = localStorage.getItem(TOPIC_STORAGE_KEY);
  } catch {
    // Сховище недоступне — стартуємо з «Усі теми».
  }
  if (storedTopic === "none" || noteGroups.has(Number(storedTopic))) {
    activeTopic = storedTopic;
  }
  renderTopics();

  try {

    pdf = await pdfjsLib.getDocument({ url: viewer.dataset.fileUrl }).promise;
    await reflow();

    const params = new URLSearchParams(location.search);
    const targetId = Number(params.get("annotation"));
    const targetPage = Number(params.get("page"));
    const lastPage = Number(viewer.dataset.lastPage);

    // ?annotation= на власну нотатку з іншої теми: показуємо всі теми, інакше мітки не буде.
    const ownTarget = annotations.get(targetId);
    if (ownTarget && !matchesTopic(ownTarget)) {
      activeTopic = "all";
      renderTopics();
    }

    // ?annotation= з чужого шару відкриває той шар; інакше — шар із ?layer=.
    const holder =
      targetId && !annotations.has(targetId)
        ? [...sharedLayers.values()].find((layer) => layer.annotations.has(targetId))
        : null;
    const initialLayer = holder?.id ?? Number(params.get("layer"));
    if (sharedLayers.has(initialLayer)) {
      showLayer(initialLayer);
    } else {
      refresh();
    }

    if (targetId && shown.has(targetId)) {
      await focusAnnotation(targetId, "auto");
    } else if (targetPage >= 1 && targetPage <= pdf.numPages) {
      await goToPage(targetPage);
    } else if (lastPage > 1 && lastPage <= pdf.numPages) {
      await goToPage(lastPage);
    }

    tracking = true;
    trackCurrentPage();
  } catch (error) {
    viewer.replaceChildren();
    const message = document.createElement("div");
    message.className = "alert alert-danger m-4";
    message.textContent = interpolate(gettext("Не вдалося відкрити документ: %(error)s"), { error: error.message }, true);
    viewer.append(message);
  }
}
