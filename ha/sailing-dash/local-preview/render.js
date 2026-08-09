// Renders each entry from window.PREVIEW_CARDS as a real custom element
// (createElement(tag)) fed the fake hass from mock-hass.js, and surfaces
// any error thrown by setConfig()/hass setter or by the element itself
// (via window.onerror / a hui-error-card-like fallback) right on the page,
// instead of a silent blank card.
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

  function renderOne(spec) {
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
    container.appendChild(block);

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

  window.addEventListener('error', (e) => {
    console.error('[preview] window.onerror:', e.message, e.filename, e.lineno);
  });

  (window.PREVIEW_CARDS || []).forEach(renderOne);
})();
