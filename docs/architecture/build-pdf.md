# Rebuilding the PDFs

Two PDFs are generated from the markdown in this directory. Both are committed
so they can be shared without a toolchain, but both are build artefacts —
**edit the markdown, never the PDF.**

| PDF | Source | Scripts |
|---|---|---|
| `Canvas-Marketing-OS-Architecture.pdf` | the numbered `00-*.md` … `19-*.md` chapter set | `build.js` + `topdf.js` |
| `Canvas-Marketing-OS-Process-Map.pdf` | the single `Comprehensive-System-Architecture-and-Process-Map.md` | `build-process-map.js` + `topdf-process-map.js` |

The two pairs share a stylesheet and mermaid theme so both documents look like
the same publication. The process-map pair differs only in that it splits ONE
file on its `## N. Title` headings instead of concatenating many files, and
that its print step also enforces the page-fit rule described below.

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

For the process map:

```bash
DOC_DATE="17 August 2026" DOC_COMMIT="$(git rev-parse --short HEAD)" \
  node build-process-map.js                # -> process-map.html
PW=playwright-core node topdf-process-map.js  # -> Canvas-Marketing-OS-Process-Map.pdf
```

Both print steps print `diagrams: N/N rendered cleanly`. If either reports
`BROKEN:`, fix the offending mermaid block before shipping the PDF.
`topdf-process-map.js` additionally reports `TOO TALL:` and exits non-zero —
see "Diagrams must fit a page" below.

## Diagrams must fit a page

The printable area is 176mm x 263mm and the `.mermaid` box costs ~8mm of it, so
a diagram whose **height/width exceeds ~1.45** is split across a page boundary
and prints cut in half. `topdf-process-map.js` fails the build on this rather
than shipping a clipped diagram.

The fixes, in order of preference:

- Flip `flowchart TB` to `flowchart LR`. A chain that stacks vertically in TB
  runs horizontally in LR, which is the shape an A4 page actually wants. This
  alone fixed six diagrams during the first process-map build.
- Split the diagram in two, with a caption on each half. Sequence diagrams
  cannot be rotated, so this is the only option for a long one.
- Drop the grouping subgraphs. Independent chains inside separate subgraphs
  stack vertically; without the subgraphs the layout engine can pack them.

**`direction` inside a subgraph does not work** as an escape hatch: mermaid
ignores it whenever that subgraph has an edge to anything outside itself, which
is true of essentially every architecture diagram. A layer stack written as
seven `direction LR` subgraphs rendered as one 5.2-ratio column.

Very wide diagrams have the mirror-image problem — they scale down to fit the
page width and become illegible. The master architecture map rendered at
7122x1842 (ratio 0.26) in TB and 3531x4052 (ratio 1.15) in LR; the LR version
fills a portrait page and is roughly twice as legible.

## Mermaid authoring constraints (learned the hard way)

These bit during the first build and will bite again:

- **No `;` inside diagram text.** Mermaid treats a semicolon as a statement
  separator, so `H->>DB: COMPLETED; advance_dependents` silently splits into
  two statements and the diagram fails to parse. Use `·` or "then".
- **`{ ... }` in sequence-diagram message text is fine on mermaid 11.** This
  entry previously said the braces were "parsed, not printed". That no longer
  holds — re-verified on mermaid 11.16 by reading the rendered SVG's own text
  content back for `{loop_id: ...}`, `{pass, violations}` and similar. Re-check
  before assuming either way if the pinned mermaid version changes.
- **ER attribute blocks must be multi-line.** One `type name` pair per line
  inside the braces; a single-line, separator-joined form does not parse.
- **Keep `gantt` section labels short** (`section Wave 1`, not
  `section Wave 1 — unblock (4 wks)`). Long labels overlap the bars in print.
- **Markdown emphasis inside a node label prints literally.** `A["**Bold**"]`
  renders the asterisks. Use `<b>` / `<i>` instead — the build sets
  `htmlLabels: true`, so HTML tags in labels work. (Mermaid's own backtick
  markdown-string syntax also works but cannot be mixed with `<br/>`.)

## Markdown authoring constraints

- An ordered list can only interrupt a paragraph if it starts with `1.`
  (CommonMark). A line beginning `0.` or `09.` after a paragraph gets absorbed
  into that paragraph. Leave a blank line, or rewrap so the line does not
  start with a number and a dot.
- A line starting with `+ ` becomes a bullet. Rewrap continuation lines so
  they do not begin with `+`, `-` or `*`.
- The `| | |` empty-header table idiom is fine — the build strips the empty
  `<thead>` and bolds the first column instead.
- **A `---` rule on the line directly after a paragraph is a setext H2**, not a
  horizontal rule — the paragraph silently becomes a heading. Always leave a
  blank line before `---`. This turned four ordinary paragraphs into 21pt
  chapter-style headings on the first process-map build.
- **Markdown emphasis in an `##` heading prints literally** in the contents
  list and chapter header, because both are HTML-escaped. `build-process-map.js`
  strips `**` and backticks from titles for exactly this reason.
