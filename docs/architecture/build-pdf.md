# Rebuilding the PDF

`Canvas-Marketing-OS-Architecture.pdf` is generated from the markdown in this
directory. It is committed so it can be shared without a toolchain, but it is
a build artefact — **edit the markdown, never the PDF.**

## What it does

1. Concatenates `0*.md` … `17-*.md` (excluding this file and `README.md`) into
   one HTML document with a cover, a table of contents and per-chapter page
   breaks.
2. Renders every ```` ```mermaid ```` fence to inline SVG with `mermaid` in a
   headless Chromium page, and **fails loudly** if any diagram produces a
   syntax-error box rather than a diagram.
3. Prints to A4 PDF via Chromium's print pipeline.

## Prerequisites

Node 20+ and a Chromium build. On a machine with Playwright's browsers
installed, the Chromium path is `$PLAYWRIGHT_BROWSERS_PATH/chromium-*/chrome-linux/chrome`.

```bash
mkdir -p /tmp/cmos-pdf && cd /tmp/cmos-pdf
npm init -y
npm install markdown-it mermaid playwright-core
```

Copy `build.js` and `topdf.js` (below) into that directory, then:

```bash
DOC_DATE="6 August 2026" node build.js     # -> book.html
PW=playwright-core node topdf.js           # -> Canvas-Marketing-OS-Architecture.pdf
```

`topdf.js` prints `diagrams: N/N rendered cleanly`. If it reports `BROKEN:`,
fix the offending mermaid block before shipping the PDF.

## Mermaid authoring constraints (learned the hard way)

These bit during the first build and will bite again:

- **No `;` inside diagram text.** Mermaid treats a semicolon as a statement
  separator, so `H->>DB: COMPLETED; advance_dependents` silently splits into
  two statements and the diagram fails to parse. Use `·` or "then".
- **No `{ ... }` in sequence-diagram message text** — the braces are parsed,
  not printed.
- **ER attribute blocks must be multi-line.** One `type name` pair per line
  inside the braces; a single-line, separator-joined form does not parse.
- **Keep `gantt` section labels short** (`section Wave 1`, not
  `section Wave 1 — unblock (4 wks)`). Long labels overlap the bars in print.

## Markdown authoring constraints

- An ordered list can only interrupt a paragraph if it starts with `1.`
  (CommonMark). A line beginning `0.` or `09.` after a paragraph gets absorbed
  into that paragraph. Leave a blank line, or rewrap so the line does not
  start with a number and a dot.
- A line starting with `+ ` becomes a bullet. Rewrap continuation lines so
  they do not begin with `+`, `-` or `*`.
- The `| | |` empty-header table idiom is fine — the build strips the empty
  `<thead>` and bolds the first column instead.
