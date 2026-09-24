const viewer = document.getElementById("pdf-viewer");

if (viewer) {
  const pdfjsLib = await import(viewer.dataset.pdfjsUrl);
  pdfjsLib.GlobalWorkerOptions.workerSrc = viewer.dataset.pdfjsWorkerUrl;

  const MIN_SCALE = 0.5;
  const MAX_SCALE = 3;

  const canAnnotate = viewer.dataset.canAnnotate === "1";
  const csrfToken = viewer.dataset.csrfToken;
  const palette = document.getElementById("highlightPalette");
  const popover = document.getElementById("annotationPopover");
  const sidebar = document.getElementById("annotationList");
  const counter = document.querySelector("[data-reader-toggle-panel] .badge");
  const filterBar = document.getElementById("annotationFilter");
  const filterChips = filterBar?.querySelector("[data-filter-chips]");
  const filterSummary = filterBar?.querySelector("[data-filter-summary]");
  const filterReset = filterBar?.querySelector("[data-filter-reset]");

  const annotations = new Map();
  const pageViewports = new Map();
  const pageDivs = new Map();

  const hiddenColors = new Set();
  const chipNodes = new Map();

  let pdf = null;
  let scale = 1;
  let pendingSelection = null;
  let activeId = null;
  let navId = null;
  let flashTimer = null;

  const dataNode = document.getElementById("annotations-data");
  if (dataNode) {
    for (const item of JSON.parse(dataNode.textContent)) {
      annotations.set(item.id, item);
    }
  }

  const paletteLabels = new Map();
  const paletteNode = document.getElementById("palette-data");
  if (paletteNode) {
    for (const [value, label] of JSON.parse(paletteNode.textContent)) {
      paletteLabels.set(value, label);
    }
  }

  const isVisible = (annotation) => !hiddenColors.has(annotation.color);

  const byPageThenId = (a, b) => a.page - b.page || a.id - b.id;

  function visibleSorted() {
    return [...annotations.values()].filter(isVisible).sort(byPageThenId);
  }

  async function request(url, method, body) {
    const response = await fetch(url, {
      method,
      credentials: "same-origin",
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

    for (const annotation of annotations.values()) {
      if (annotation.page !== pageNumber || !isVisible(annotation)) {
        continue;
      }

      for (const coords of annotation.coords || []) {
        const rect = toViewportRect(coords, viewport);
        const mark = document.createElement("div");
        mark.className = "annotation-rect";
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
    badge.textContent = `с. ${annotation.page}`;

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

    if (counter) {

      counter.textContent = hiddenColors.size
        ? `${sorted.length}/${annotations.size}`
        : String(annotations.size);
    }
    if (!sidebar) {
      return;
    }

    if (!sorted.length) {

      if (sidebar.dataset.jsOwned !== "1" && !hiddenColors.size) {
        return;
      }

      sidebar.dataset.jsOwned = "1";
      const empty = document.createElement("p");
      empty.className = "text-body-secondary small text-center py-4 mb-0";
      empty.textContent = hiddenColors.size
        ? "Усі мітки сховані фільтром."
        : "Нотаток ще немає.";
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
    chip.title = "Клік — сховати/показати. Подвійний клік — лише цей колір.";

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
    for (const annotation of annotations.values()) {
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
      filterSummary.textContent = hiddenColors.size
        ? `${visibleSorted().length} з ${annotations.size}`
        : `${annotations.size} міток`;
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

  async function focusAnnotation(id, behavior = "smooth") {
    const annotation = annotations.get(id);
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
      });
      annotations.set(created.id, created);

      hiddenColors.delete(created.color);
      refresh();
      window.getSelection()?.removeAllRanges();
    } catch (error) {
      alert(`Не вдалося зберегти виділення: ${error.message}`);
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

  viewer.addEventListener("reader:page", (event) => {
    pageDivs.get(event.detail.page)?.scrollIntoView({ behavior: "smooth", block: "start" });
  });

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
    for (const annotation of annotations.values()) {
      if (annotation.color !== chip.dataset.filterColor) {
        hiddenColors.add(annotation.color);
      }
    }
    refresh();
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
    await reflow();
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
        if (mark) {
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
        patchActive({ color: swatch.dataset.color }, "Колір змінено");
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
        patchActive({ note_text: event.target.value }, "Збережено");
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        hidePalette();
        hidePopover();
      }
    });
  }

  try {

    pdf = await pdfjsLib.getDocument({ url: viewer.dataset.fileUrl }).promise;
    await reflow();
    refresh();

    const params = new URLSearchParams(location.search);
    const targetId = Number(params.get("annotation"));
    const targetPage = Number(params.get("page"));

    if (targetId && annotations.has(targetId)) {
      await focusAnnotation(targetId, "auto");
    } else if (targetPage >= 1 && targetPage <= pdf.numPages) {
      await renderPage(targetPage);
      pageDivs.get(targetPage)?.scrollIntoView({ block: "start" });
    }
  } catch (error) {
    viewer.replaceChildren();
    const message = document.createElement("div");
    message.className = "alert alert-danger m-4";
    message.textContent = `Не вдалося відкрити документ: ${error.message}`;
    viewer.append(message);
  }
}
