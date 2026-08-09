// Serves this folder over http:// (custom elements/modules don't reliably
// load from file:// due to CORS on module scripts) and takes a full-page
// screenshot + dumps the browser console, so the whole preview can be
// checked headlessly without opening a real browser window.
//
// Usage: node run-preview.js [outputPngPath]
// Requires: `npm install playwright http-server` (or any local static
// server - see README.md for the manual "open in a real browser" option,
// which needs no Node/Playwright at all).

const { chromium } = require('playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = 8977;
const ROOT = __dirname;
const OUT = process.argv[2] || path.join(ROOT, 'preview-output.png');

const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.png': 'image/png' };

function startServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let filePath = path.join(ROOT, decodeURIComponent(req.url.split('?')[0]));
      if (req.url === '/') filePath = path.join(ROOT, 'index.html');
      fs.readFile(filePath, (err, data) => {
        if (err) {
          res.writeHead(404);
          res.end('Not found: ' + filePath);
          return;
        }
        res.writeHead(200, { 'Content-Type': MIME[path.extname(filePath)] || 'application/octet-stream' });
        res.end(data);
      });
    });
    server.listen(PORT, () => resolve(server));
  });
}

(async () => {
  const server = await startServer();
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 760, height: 2200 } });

  const logs = [];
  page.on('console', (msg) => logs.push(`[${msg.type()}] ${msg.text()}`));
  page.on('pageerror', (err) => logs.push(`[pageerror] ${err.message}`));

  await page.goto(`http://localhost:${PORT}/index.html`, { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForTimeout(3000);

  const statuses = await page.$$eval('.card-status', (nodes) =>
    nodes.map((n) => ({ text: n.textContent, ok: n.classList.contains('ok') }))
  );

  await page.screenshot({ path: OUT, fullPage: true });

  console.log('--- card statuses ---');
  statuses.forEach((s, i) => console.log(`${i}: [${s.ok ? 'OK' : 'FAIL'}] ${s.text}`));
  console.log('--- console logs ---');
  console.log(logs.join('\n'));
  console.log('--- screenshot saved to ---', OUT);

  await browser.close();
  server.close();

  const anyFail = statuses.some((s) => !s.ok);
  process.exit(anyFail ? 1 : 0);
})();
