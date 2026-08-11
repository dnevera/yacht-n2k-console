// Wave vector arrows + "Now" marker for the wave chart.
({ vars }) => {
  const walk = (root) => {
    root.querySelectorAll('plotly-graph').forEach((el) => {
      const sr = el.shadowRoot;
      if (!sr || sr.querySelector('style[data-now-radius]')) return;
      const st = document.createElement('style');
      st.setAttribute('data-now-radius', '');
      st.textContent = '.annotation rect.bg { rx: 4px; ry: 4px; }';
      sr.appendChild(st);
    });
    root.querySelectorAll('*').forEach((el) => {
      if (el.shadowRoot) walk(el.shadowRoot);
    });
  };
  try {
    walk(document);
  } catch (e) {}
  const waveHeightColor = (v) => {
    const stops = [
      [0.3, '#b0e2ff'],
      [0.6, '#61c4e0'],
      [1, '#4bbf7a'],
      [1.5, '#a8d048'],
      [2, '#f5e642'],
      [3, '#f2a93b'],
      [4, '#eb5c2a'],
      [5, '#d62828'],
    ];
    for (const [max, color] of stops) if (v < max) return color;
    return '#8e1b8e';
  };
  const toArrows = (xs, ys, dirs) =>
    (xs || []).map((x, i) => {
      const d = dirs[i] || 0;
      const rad = ((d + 180) * Math.PI) / 180;
      const len = 14;
      return {
        x,
        y: ys[i],
        xref: 'x',
        yref: 'y',
        ax: -len * Math.sin(rad),
        ay: len * Math.cos(rad),
        axref: 'pixel',
        ayref: 'pixel',
        showarrow: true,
        arrowhead: 2,
        arrowsize: 1,
        arrowwidth: 1.5,
        arrowcolor: waveHeightColor(ys[i] || 0),
        captureevents: false,
      };
    });
  const wh = vars.waveHeight || { xs: [], ys: [] };
  return [
    ...toArrows(wh.xs, wh.ys, vars.waveDir || []),
    {
      xref: 'x',
      yref: 'paper',
      x: new Date(),
      y: 0.99,
      yanchor: 'top',
      xanchor: 'right',
      text: 'Now',
      textangle: -90,
      showarrow: false,
      xshift: -2,
      bgcolor: '#ffffff',
      borderpad: 4,
      font: { color: '#000000', size: 10 },
    },
    {
      xref: 'paper',
      yref: 'paper',
      x: 0.01,
      y: 0.97,
      xanchor: 'left',
      yanchor: 'top',
      text: '▲ N &nbsp;&nbsp; ▼ S',
      showarrow: false,
      font: { color: '#90a4ae', size: 10 },
    },
  ];
}
