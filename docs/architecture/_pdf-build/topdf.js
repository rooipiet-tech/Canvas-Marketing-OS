const path = require('path');
const { chromium } = require(process.env.PW || '/opt/pw-browsers/playwright-stub');

(async () => {
  const browser = await chromium.launch({
    executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none'],
  });
  const page = await browser.newPage();
  page.on('console', m => { if (m.type() === 'error') console.log('PAGE ERR:', m.text()); });

  const file = 'file://' + path.join(__dirname, 'book.html');
  await page.goto(file, { waitUntil: 'load', timeout: 180000 });

  // wait for mermaid to finish rendering every diagram
  await page.waitForFunction(() => window.__mermaidDone !== undefined, { timeout: 60000 });
  await page.evaluate(() => window.__mermaidDone);
  await page.waitForFunction(() => {
    const blocks = document.querySelectorAll('pre.mermaid');
    let rendered = 0;
    blocks.forEach(b => { if (b.querySelector('svg')) rendered++; });
    return rendered === blocks.length;
  }, { timeout: 120000 }).catch(() => console.log('WARN: not every diagram rendered'));

  const stats = await page.evaluate(() => {
    const all = document.querySelectorAll('pre.mermaid');
    let ok = 0; const broken = [];
    all.forEach((b, i) => {
      const svg = b.querySelector('svg');
      if (!svg) { broken.push('no-svg #' + i); return; }
      const t = svg.textContent || '';
      if (/Syntax error|mermaid version/i.test(t)) {
        broken.push('error-box in ' + (b.closest('section') || {}).id);
      } else ok++;
    });
    return { total: all.length, ok, broken };
  });
  console.log(`diagrams: ${stats.ok}/${stats.total} rendered cleanly`);
  if (stats.broken.length) console.log('BROKEN:', stats.broken.join(' | '));

  await page.emulateMedia({ media: 'print' });
  await page.pdf({
    path: path.join(__dirname, 'Canvas-Marketing-OS-Architecture.pdf'),
    format: 'A4',
    printBackground: true,
    displayHeaderFooter: true,
    headerTemplate: '<div></div>',
    footerTemplate: `<div style="width:100%;font-size:8px;color:#8a95a1;
        font-family:-apple-system,Segoe UI,Roboto,sans-serif;padding:0 17mm;
        display:flex;justify-content:space-between;">
        <span>Canvas Marketing OS — Architecture &amp; Product Reference</span>
        <span class="pageNumber"></span></div>`,
    margin: { top: '19mm', bottom: '15mm', left: '17mm', right: '17mm' },
  });

  await browser.close();
  console.log('PDF written');
})().catch(e => { console.error(e); process.exit(1); });
