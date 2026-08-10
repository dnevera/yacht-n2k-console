// Shared plotly-graph "$fn" body for on_dblclick handlers.
// Resets zoom/pan on every plotly-graph instance found in the document
// (including inside nested shadow roots) by clicking its hidden reset button.
// Injected via build.py into cards that used to duplicate this snippet.
() => () => {
  const found = [];
  const walk = (root) => {
    root.querySelectorAll('plotly-graph').forEach((e) => found.push(e));
    root.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) walk(e.shadowRoot); });
  };
  walk(document);
  found.forEach((el) => {
    const btn = el.shadowRoot && el.shadowRoot.querySelector('button#reset');
    if (btn && !btn.classList.contains('hidden')) btn.click();
  });
}
