// Print process-map.html to A4 PDF, failing loudly if any mermaid diagram
// rendered as a syntax-error box rather than a diagram.
//
// Companion to topdf.js (which prints the 00-19 chapter set). Same Chromium
// print pipeline, different input/output filenames and footer title.
//
// Writes the PDF to docs/architecture/ (one level up, beside the markdown it
// was built from) rather than leaving it in this build directory, so the
// shipped artefact lands where a reader looks for it with no manual move.

const path = require('path');
const { chromium } = require(process.env.PW || '/opt/pw-browsers/playwright-stub');

const CHROME = process.env.CHROME ||
  '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';

(async () => {
  const browser = await chromium.launch({
    executablePath: CHROME,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none'],
  });
  const page = await browser.newPage();
  page.on('console', m => { if (m.type() === 'error') console.log('PAGE ERR:', m.text()); });

  const file = 'file://' + path.join(__dirname, 'process-map.html');
  await page.goto(file, { waitUntil: 'load', timeout: 180000 });

  await page.waitForFunction(() => window.__mermaidDone !== undefined, { timeout: 60000 });
  await page.evaluate(() => window.__mermaidDone);
  await page.waitForFunction(() => {
    const blocks = document.querySelectorAll('pre.mermaid');
    let rendered = 0;
    blocks.forEach(b => { if (b.querySelector('svg')) rendered++; });
    return rendered === blocks.length;
  }, { timeout: 120000 }).catch(() => console.log('WARN: not every diagram rendered'));

  // Two checks, both of which have caught real defects:
  //
  //  1. A mermaid syntax-error box rendered instead of a diagram.
  //  2. A diagram too tall to fit one page. The printable area is
  //     176mm x 263mm (A4 less the margins below); the .mermaid box costs
  //     ~8mm of that, so a diagram whose height/width exceeds ~1.45 gets
  //     split across a page boundary and is cut in half. The usual cause is
  //     a `flowchart TB` whose chains stack vertically -- flipping it to
  //     `flowchart LR`, or splitting it, is the fix. Note that `direction`
  //     inside a subgraph is IGNORED by mermaid whenever that subgraph has
  //     edges to anything outside it, so it cannot be used to fix this.
  const MAX_ASPECT = 1.45;

  const stats = await page.evaluate((MAX_ASPECT) => {
    const all = document.querySelectorAll('pre.mermaid');
    let ok = 0; const broken = []; const oversized = [];
    all.forEach((b, i) => {
      const svg = b.querySelector('svg');
      const where = '#' + (i + 1) + ' in ' + ((b.closest('section') || {}).id || '?');
      if (!svg) { broken.push('no-svg ' + where); return; }
      const t = svg.textContent || '';
      if (/Syntax error|mermaid version/i.test(t)) {
        broken.push('error-box ' + where);
        return;
      }
      ok++;
      const vb = (svg.getAttribute('viewBox') || '').split(/\s+/).map(Number);
      const w = vb[2] || 0, h = vb[3] || 0;
      if (w && h / w > MAX_ASPECT) {
        oversized.push(where + ' ar=' + (h / w).toFixed(2));
      }
    });
    return { total: all.length, ok, broken, oversized };
  }, MAX_ASPECT);

  console.log(`diagrams: ${stats.ok}/${stats.total} rendered cleanly`);
  if (stats.broken.length) {
    console.log('BROKEN:', stats.broken.join(' | '));
    await browser.close();
    process.exit(1);
  }
  if (stats.oversized.length) {
    console.log(`TOO TALL (will split across pages, max ar ${MAX_ASPECT}):`,
      stats.oversized.join(' | '));
    await browser.close();
    process.exit(1);
  }

  await page.emulateMedia({ media: 'print' });
  await page.pdf({
    path: path.join(__dirname, '..', 'Canvas-Marketing-OS-Process-Map.pdf'),
    format: 'A4',
    printBackground: true,
    displayHeaderFooter: true,
    headerTemplate: '<div></div>',
    footerTemplate: `<div style="width:100%;font-size:8px;color:#8a95a1;
        font-family:-apple-system,Segoe UI,Roboto,sans-serif;padding:0 17mm;
        display:flex;justify-content:space-between;">
        <span>Canvas Marketing OS &mdash; System Architecture &amp; Process Map</span>
        <span class="pageNumber"></span></div>`,
    margin: { top: '19mm', bottom: '15mm', left: '17mm', right: '17mm' },
  });

  await browser.close();
  console.log('PDF written');
})().catch(e => { console.error(e); process.exit(1); });
