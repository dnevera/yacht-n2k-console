// Renders window.PREVIEW_SECTIONS (falling back to the flat window.PREVIEW_CARDS
// list for older card-configs.js builds) as real custom elements
// (createElement(tag)) fed the fake hass from mock-hass.js, and surfaces
// any error thrown by setConfig()/hass setter or by the element itself
// (via window.onerror / a hui-error-card-like fallback) right on the page,
// instead of a silent blank card.
//
// Unlike a plain list of cards, PREVIEW_SECTIONS mirrors the real Lovelace
// layout produced by build/dashboard-sailing.yaml: each source YAML file
// (e.g. 04_wind.yaml) becomes a titled `.preview-section`, and every
// `type: grid` block inside it becomes a `.preview-grid` with the same
// column count (`grid_options.columns`) and width (`column_span`) as the
// real dashboard, so cards that are meant to sit side by side in a grid
// row are rendered side by side here too.
//
// Note: vendor/compass-card.js is loaded as an ES module (`<script
// type="module">`), which is deferred and defines its custom element
// asynchronously relative to this plain script - customElements.whenDefined
// is used below so we don't race that.

(function () {
  const container = document.getElementById('cards');

  function withTimeout(promise, ms, tag) {
    return Promise.race([
      promise,
      new Promise((_, reject) => setTimeout(() => reject(new Error('customElements.whenDefined("' + tag + '") timed out after ' + ms + 'ms - JS bundle failed to load/register?')), ms)),
    ]);
  }

  function renderCard(spec, parent) {
    const block = document.createElement('div');
    block.className = 'card-block';
    const heading = document.createElement('h2');
    heading.textContent = spec.title + '  <' + spec.tag + '>';
    const status = document.createElement('div');
    status.className = 'card-status';
    const slot = document.createElement('div');
    slot.className = 'card-slot';
    block.appendChild(heading);
    block.appendChild(status);
    block.appendChild(slot);
    parent.appendChild(block);

    withTimeout(customElements.whenDefined(spec.tag), 5000, spec.tag)
      .then(() => {
        const el = document.createElement(spec.tag);
        // Append BEFORE setConfig/hass - some cards (e.g. plotly-graph-
        // card, which measures its own DOM node during connectedCallback)
        // need to already be connected to the document at that point.
        slot.appendChild(el);
        el.hass = window.mockHass;
        el.setConfig(spec.config);
        // Some cards only pick up hass after setConfig (order matters for
        // a few implementations) - set it again to be safe.
        el.hass = window.mockHass;
        status.textContent = 'OK - rendered without throwing';
        status.className = 'card-status ok';
      })
      .catch((err) => {
        status.textContent = 'ERROR: ' + (err && err.message ? err.message : err);
        status.className = 'card-status error';
        console.error('[preview]', spec.title, err);
      });
  }

  function renderGrid(grid, parent) {
    const gridEl = document.createElement('div');
    gridEl.className = 'preview-grid';
    // Mirror the real grid-card layout: `grid_options.columns` controls how
    // many of the 1..N "columns" a card can span (Lovelace section grid
    // uses a 4/6/12/... column model depending on `columns`), while
    // `column_span` controls how wide the whole grid block is relative to
    // sibling grids in the same section. We approximate both with a CSS
    // grid so the local-preview visually matches the dashboard.
    const columns = grid.columns || 1;
    gridEl.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
    if (grid.column_span) {
      gridEl.style.gridColumn = `span ${grid.column_span}`;
    }
    parent.appendChild(gridEl);
    grid.cards.forEach((spec) => renderCard(spec, gridEl));
  }

  function renderSection(section) {
    const sectionEl = document.createElement('section');
    sectionEl.className = 'preview-section';
    const title = document.createElement('h1');
    title.className = 'preview-section-title';
    title.textContent = section.title + ' (' + section.source + ')';
    sectionEl.appendChild(title);
    container.appendChild(sectionEl);
    section.grids.forEach((grid) => renderGrid(grid, sectionEl));
  }

  window.addEventListener('error', (e) => {
    console.error('[preview] window.onerror:', e.message, e.filename, e.lineno);
  });

  if (Array.isArray(window.PREVIEW_SECTIONS) && window.PREVIEW_SECTIONS.length > 0) {
    window.PREVIEW_SECTIONS.forEach(renderSection);
  } else {
    // Fallback for older card-configs.js builds that only expose a flat
    // window.PREVIEW_CARDS list without section/grid grouping.
    (window.PREVIEW_CARDS || []).forEach((spec) => renderCard(spec, container));
  }
})();
