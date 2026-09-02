// Build a single self-contained HTML from docs/architecture/*.md
const fs = require('fs');
const path = require('path');
const MarkdownIt = require('markdown-it');

const DOCS = process.env.DOCS || path.resolve(__dirname, '..');
const OUT = path.join(__dirname, 'book.html');

const md = new MarkdownIt({ html: true, linkify: false, typographer: false });

// Render ```mermaid fences as <pre class="mermaid"> so mermaid.js picks them up.
const defaultFence = md.renderer.rules.fence.bind(md.renderer.rules);
md.renderer.rules.fence = (tokens, idx, options, env, self) => {
  const token = tokens[idx];
  if ((token.info || '').trim() === 'mermaid') {
    return `<pre class="mermaid">${md.utils.escapeHtml(token.content)}</pre>`;
  }
  return defaultFence(tokens, idx, options, env, self);
};

const files = fs.readdirSync(DOCS)
  // remediation-backlog.md is a working execution list, not a reference
  // chapter: it churns as items land, and its H1 has no "NN — Title" shape,
  // so it renders with the whole title in the chapter-number slot. Excluded
  // deliberately; it lives beside the pack, not inside it.
  .filter(f => f.endsWith('.md') &&
    !['README.md', 'build-pdf.md', 'remediation-backlog.md'].includes(f))
  .sort();

const mermaidJs = fs.readFileSync(
  path.join(__dirname, 'node_modules/mermaid/dist/mermaid.min.js'), 'utf8');

// ---- chapter titles from each file's H1 -------------------------------------
const chapters = files.map(f => {
  const raw = fs.readFileSync(path.join(DOCS, f), 'utf8');
  const m = raw.match(/^#\s+(.+)$/m);
  const full = m ? m[1].trim() : f;
  const parts = full.split('—');
  return {
    file: f,
    num: (parts[0] || '').trim(),
    title: (parts.slice(1).join('—') || full).trim(),
    raw,
    id: 'ch-' + f.replace(/\.md$/, ''),
  };
});

// ---- body -------------------------------------------------------------------
let body = '';
for (const ch of chapters) {
  // strip the H1 (we render our own chapter header) and the leading italic note
  let content = ch.raw.replace(/^#\s+.+$/m, '');
  let html = md.render(content);
  // demote heading levels by one so chapter titles stay dominant
  html = html
    .replace(/<h5>/g, '<h6>').replace(/<\/h5>/g, '</h6>')
    .replace(/<h4>/g, '<h5>').replace(/<\/h4>/g, '</h5>')
    .replace(/<h3>/g, '<h4>').replace(/<\/h3>/g, '</h4>')
    .replace(/<h2>/g, '<h3>').replace(/<\/h2>/g, '</h3>');

  // Drop header rows whose cells are all empty (the `| | |` info-table idiom),
  // which would otherwise print as a stray grey bar.
  html = html.replace(/<thead>\s*<tr>((?:\s*<th[^>]*>\s*<\/th>)+)\s*<\/tr>\s*<\/thead>/g, '');

  body += `<section class="chapter" id="${ch.id}">
  <header class="chapter-head">
    <div class="chapter-num">${md.utils.escapeHtml(ch.num)}</div>
    <h2>${md.utils.escapeHtml(ch.title)}</h2>
  </header>
  ${html}
</section>\n`;
}

// ---- table of contents ------------------------------------------------------
const toc = chapters.map(ch => `
  <li>
    <span class="toc-num">${md.utils.escapeHtml(ch.num)}</span>
    <a href="#${ch.id}">${md.utils.escapeHtml(ch.title)}</a>
  </li>`).join('');

const today = process.env.DOC_DATE || '';

const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Canvas Marketing OS — Architecture &amp; Product Reference</title>
<style>
:root{
  --ink:#14181d; --muted:#5b6672; --faint:#8a95a1;
  --rule:#d9dee4; --rule-soft:#eceff3;
  --accent:#0f5c4a; --accent-soft:#e8f2ef;
  --code-bg:#f5f7f9; --warn:#8a4b08;
}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact; print-color-adjust:exact}
body{
  margin:0; color:var(--ink); background:#fff;
  font:10.5pt/1.55 "Charter","Bitstream Charter","Georgia","Times New Roman",serif;
}
h1,h2,h3,h4,h5,h6,.toc-num,.chapter-num,th,code,pre,.tag{
  font-family:-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
}

/* ---------- cover ---------- */
.cover{
  height:247mm; display:flex; flex-direction:column; justify-content:space-between;
  page-break-after:always; padding:4mm 0 0;
}
.cover-rule{height:5px;background:var(--accent);width:56mm;margin-bottom:14mm}
.cover-eyebrow{
  font-family:-apple-system,"Segoe UI",Roboto,sans-serif; font-size:9.5pt;
  letter-spacing:.22em; text-transform:uppercase; color:var(--accent); font-weight:700;
}
.cover h1{
  font-size:34pt; line-height:1.1; margin:8mm 0 4mm; font-weight:700; letter-spacing:-.01em;
}
.cover h1 small{display:block;font-size:17pt;font-weight:400;color:var(--muted);margin-top:5mm;letter-spacing:0}
.cover-lede{font-size:11.5pt;color:var(--muted);max-width:118mm;line-height:1.6}
.cover-meta{
  border-top:1px solid var(--rule); padding-top:5mm; font-size:9pt; color:var(--faint);
  font-family:-apple-system,"Segoe UI",Roboto,sans-serif;
  display:flex; justify-content:space-between; gap:8mm; flex-wrap:wrap;
}
.cover-meta b{color:var(--ink);font-weight:600}
.cover-stats{display:flex;gap:12mm;margin:10mm 0 0;flex-wrap:wrap}
.cover-stat{border-left:3px solid var(--accent-soft);padding-left:4mm}
.cover-stat .n{font-family:-apple-system,"Segoe UI",Roboto,sans-serif;font-size:20pt;font-weight:700;color:var(--accent);display:block;line-height:1.1}
.cover-stat .l{font-size:8.5pt;color:var(--muted);font-family:-apple-system,"Segoe UI",Roboto,sans-serif;text-transform:uppercase;letter-spacing:.08em}

/* ---------- toc ---------- */
.toc{page-break-after:always}
.toc h2{font-size:15pt;margin:0 0 7mm;padding-bottom:3mm;border-bottom:2px solid var(--ink);letter-spacing:-.01em}
.toc ol{list-style:none;margin:0;padding:0}
.toc li{display:flex;gap:5mm;align-items:baseline;padding:2.6mm 0;border-bottom:1px solid var(--rule-soft)}
.toc-num{color:var(--accent);font-weight:700;font-size:9.5pt;min-width:9mm;font-variant-numeric:tabular-nums}
.toc a{color:var(--ink);text-decoration:none;font-size:11pt}

/* ---------- chapters ---------- */
.chapter{page-break-before:always}
.chapter-head{margin:0 0 7mm;padding-bottom:4mm;border-bottom:2px solid var(--ink)}
.chapter-num{
  font-size:8.5pt;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);
  font-weight:700;margin-bottom:2mm;
}
.chapter-head h2{font-size:21pt;margin:0;line-height:1.15;font-weight:700;letter-spacing:-.01em}

h3{font-size:13.5pt;margin:9mm 0 3mm;line-height:1.25;page-break-after:avoid;letter-spacing:-.005em}
h4{font-size:11.5pt;margin:7mm 0 2.5mm;page-break-after:avoid;color:#1d2530}
h5,h6{font-size:10.5pt;margin:5mm 0 2mm;page-break-after:avoid;color:var(--muted)}
p{margin:0 0 3.2mm;orphans:3;widows:3}
ul,ol{margin:0 0 3.5mm;padding-left:6mm}
li{margin:0 0 1.4mm}
li>ul,li>ol{margin-top:1.4mm}
a{color:var(--accent);text-decoration:none}
strong{font-weight:700}
hr{border:0;border-top:1px solid var(--rule);margin:7mm 0}

blockquote{
  margin:4mm 0; padding:3mm 0 3mm 5mm; border-left:3px solid var(--accent);
  background:var(--accent-soft); color:#123f34; page-break-inside:avoid;
}
blockquote p:last-child{margin-bottom:0}
blockquote p{padding-right:4mm}

code{
  background:var(--code-bg); padding:.5mm 1.1mm; border-radius:2px;
  font-size:8.6pt; color:#0d3b30; border:1px solid var(--rule-soft);
}
pre{
  background:var(--code-bg); border:1px solid var(--rule); border-left:3px solid var(--faint);
  padding:3mm 3.5mm; overflow:hidden; font-size:8pt; line-height:1.45;
  page-break-inside:avoid; margin:3.5mm 0; border-radius:2px;
  white-space:pre-wrap; word-break:break-word;
}
pre code{background:none;border:0;padding:0;font-size:8pt;color:#1d2530}

table{
  border-collapse:collapse; width:100%; margin:4mm 0; font-size:8.6pt;
  page-break-inside:avoid; line-height:1.4;
}
th{
  background:#eef2f5; text-align:left; font-weight:700; font-size:8.2pt;
  text-transform:uppercase; letter-spacing:.04em; color:#2b3542;
  border-bottom:2px solid var(--rule); padding:2mm 2.2mm;
}
td{border-bottom:1px solid var(--rule-soft); padding:2mm 2.2mm; vertical-align:top}
table:not(:has(thead)) tr:first-child td:first-child{font-weight:700;color:#2b3542}
tr:nth-child(even) td{background:#fafbfc}
table code{font-size:7.8pt}

/* mermaid */
.mermaid{
  background:#fff; border:1px solid var(--rule-soft); padding:4mm; margin:5mm 0;
  text-align:center; page-break-inside:avoid; border-radius:3px;
}
.mermaid svg{max-width:100%!important;height:auto!important}

@page{ size:A4; margin:19mm 17mm 17mm }
</style>
</head><body>

<div class="cover">
  <div>
    <div class="cover-rule"></div>
    <div class="cover-eyebrow">Enterprise Architecture Reference</div>
    <h1>Canvas Marketing OS
      <small>Architecture, Product &amp; Operating Model — reverse-engineered from source</small>
    </h1>
    <p class="cover-lede">
      A complete technical and commercial reference for the Canvas Marketing OS
      platform, derived entirely from the source tree. No prior documentation was
      assumed; the code is treated as the single source of truth. Every claim
      cites a file. Inference is marked <b>[INFERRED]</b>.
    </p>
    <div class="cover-stats">
      <div class="cover-stat"><span class="n">18</span><span class="l">Documents</span></div>
      <div class="cover-stat"><span class="n">917</span><span class="l">Files read</span></div>
      <div class="cover-stat"><span class="n">8</span><span class="l">Services</span></div>
      <div class="cover-stat"><span class="n">27</span><span class="l">Tables / 5 schemas</span></div>
      <div class="cover-stat"><span class="n">23</span><span class="l">Agent packages</span></div>
    </div>
  </div>
  <div class="cover-meta">
    <span><b>Repository</b> rooipiet-tech/Canvas-Marketing-OS</span>
    <span><b>Branch</b> claude/platform-reverse-engineering-19ytw9</span>
    <span><b>Prepared</b> ${today}</span>
  </div>
</div>

<div class="toc">
  <h2>Contents</h2>
  <ol>${toc}</ol>
</div>

${body}

<script>${mermaidJs}</script>
<script>
  mermaid.initialize({
    startOnLoad:false, theme:'base', securityLevel:'loose',
    themeVariables:{
      fontFamily:'-apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif',
      fontSize:'14px',
      primaryColor:'#e8f2ef', primaryTextColor:'#14181d', primaryBorderColor:'#0f5c4a',
      lineColor:'#5b6672', secondaryColor:'#f5f7f9', tertiaryColor:'#fafbfc',
      clusterBkg:'#fafbfc', clusterBorder:'#d9dee4'
    },
    flowchart:{useMaxWidth:true,htmlLabels:true,curve:'basis'},
    sequence:{useMaxWidth:true,wrap:true,width:150},
    er:{useMaxWidth:true}, gantt:{useMaxWidth:true}
  });
  window.__mermaidDone = (async () => {
    try { await mermaid.run({ querySelector:'.mermaid' }); }
    catch(e){ console.error('mermaid:', e && e.message); }
    return true;
  })();
</script>
</body></html>`;

fs.writeFileSync(OUT, html);
console.log('wrote', OUT, (html.length/1024/1024).toFixed(2)+'MB', '·', chapters.length, 'chapters');
