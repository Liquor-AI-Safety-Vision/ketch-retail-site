"""Render docs/design.md to a PDF.

Markdown -> HTML with python-markdown, then Chromium (via puppeteer) prints it.
Mermaid fences become <pre class="mermaid"> and are rendered in the browser from
the local mermaid bundle before printing, so the diagrams survive into the PDF
instead of degrading to code blocks.

    python3 tools/pdf/build.py [in.md] [out.pdf]
"""
import pathlib
import re
import subprocess
import sys

import markdown

ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).resolve().parent
MERMAID = HERE / "node_modules/mermaid/dist/mermaid.min.js"

CSS = """
@page { size: A4; margin: 20mm 18mm; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
       font-size: 10.5pt; line-height: 1.55; color: #1a1a1a; }
h1 { font-size: 21pt; margin: 0 0 4pt; letter-spacing: -0.3pt; }
h2 { font-size: 15pt; margin: 22pt 0 7pt; padding-bottom: 4pt;
     border-bottom: 1.5px solid #333; break-after: avoid; }
h3 { font-size: 12pt; margin: 15pt 0 5pt; break-after: avoid; }
h4 { font-size: 10.5pt; margin: 12pt 0 4pt; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
code { font-family: "SF Mono", Menlo, monospace; font-size: 8.7pt;
       background: #f2f2f4; padding: 1px 4px; border-radius: 3px; }
pre { background: #f7f7f9; border: 1px solid #e0e0e4; border-left: 3px solid #888;
      border-radius: 3px; padding: 8pt 10pt; overflow: hidden;
      font-size: 8.3pt; line-height: 1.4; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: inherit; }
table { border-collapse: collapse; width: 100%; margin: 9pt 0;
        font-size: 9pt; break-inside: avoid; }
th, td { border: 1px solid #ccc; padding: 4.5pt 6pt; text-align: left;
         vertical-align: top; }
th { background: #ececf0; font-weight: 600; }
tbody tr:nth-child(even) { background: #fafafb; }
blockquote { border-left: 3px solid #bbb; margin: 9pt 0; padding: 2pt 0 2pt 12pt;
             color: #444; }
hr { border: none; border-top: 1px solid #ddd; margin: 16pt 0; }
a { color: #1a4f8a; text-decoration: none; }
.mermaid { text-align: center; margin: 12pt 0; break-inside: avoid; }
/* cap the height too, or a tall vertical flowchart expands to fill a whole page */
.mermaid svg { max-width: 100%; max-height: 108mm; width: auto; height: auto; }
"""

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title><style>{css}</style></head><body>
{body}
<script>{mermaid_js}</script>
<script>
mermaid.initialize({{ startOnLoad: false, theme: 'neutral',
  flowchart: {{ useMaxWidth: true, htmlLabels: true }},
  themeVariables: {{ fontSize: '13px', fontFamily: 'Helvetica Neue, Arial' }} }});
window.__ready = false;
mermaid.run({{ querySelector: '.mermaid' }}).then(() => {{ window.__ready = true; }})
  .catch(e => {{ console.error(e); window.__ready = true; }});
</script></body></html>"""

RENDER_JS = """
const puppeteer = require(process.argv[2]);
const running = process.argv[5];           // header text, taken from the H1
(async () => {
  const browser = await puppeteer.launch({ headless: 'shell' });
  const page = await browser.newPage();
  await page.goto('file://' + process.argv[3], { waitUntil: 'networkidle0' });
  await page.waitForFunction('window.__ready === true', { timeout: 60000 });
  await page.pdf({ path: process.argv[4], format: 'A4', printBackground: true,
                   displayHeaderFooter: true,
                   headerTemplate: '<div style="font-size:7pt;color:#888;width:100%;'
                     + 'text-align:right;padding:0 18mm;">' + running + '</div>',
                   footerTemplate: '<div style="font-size:7pt;color:#888;width:100%;'
                     + 'text-align:center;">Tài liệu thiết kế bài toán và giải pháp kỹ '
                     + 'thuật &nbsp;·&nbsp; <span class="pageNumber"></span>/'
                     + '<span class="totalPages"></span></div>',
                   margin: { top: '18mm', bottom: '16mm', left: '18mm', right: '18mm' } });
  await browser.close();
})();
"""


def main():
    src = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "docs/design.md")
    out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else ROOT / "docs/design.pdf")
    text = src.read_text(encoding="utf-8")

    # hold mermaid fences aside; python-markdown would emit them as <code>
    blocks = []

    def stash(m):
        blocks.append(m.group(1))
        return f"\n@@MERMAID{len(blocks) - 1}@@\n"

    text = re.sub(r"```mermaid\n(.*?)```", stash, text, flags=re.S)

    html = markdown.markdown(
        text, extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"])
    for i, code in enumerate(blocks):
        html = html.replace(f"<p>@@MERMAID{i}@@</p>",
                            f'<pre class="mermaid">{code}</pre>')

    m = re.search(r"^#\s+(.+)$", src.read_text(encoding="utf-8"), re.M)
    title = m.group(1).strip() if m else src.stem
    page = PAGE.format(title=title, css=CSS, body=html,
                       mermaid_js=MERMAID.read_text(encoding="utf-8"))
    tmp = HERE / "_render.html"
    tmp.write_text(page, encoding="utf-8")

    js = HERE / "_render.js"
    js.write_text(RENDER_JS, encoding="utf-8")
    subprocess.run(
        ["node", str(js), str(HERE / "node_modules/puppeteer"), str(tmp), str(out),
         title.upper()],
        check=True)
    tmp.unlink(); js.unlink()
    print(f"{out}  ({out.stat().st_size / 1e6:.2f} MB, {len(blocks)} diagrams)")


if __name__ == "__main__":
    main()
