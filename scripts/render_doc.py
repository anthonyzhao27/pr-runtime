#!/usr/bin/env python3
"""Render a docs/*.md file (with ```mermaid blocks) to a standalone HTML page and open it.
Usage: scripts/render_doc.py docs/CRASH-COURSE.md [out.html]
Uses marked + mermaid from a CDN; no local install."""
import html, json, subprocess, sys
from pathlib import Path

src = Path(sys.argv[1])
out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.home() / "Desktop" / (src.stem + ".html")
md = src.read_text()
page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(src.stem)}</title>
<style>
 body{{max-width:980px;margin:40px auto;padding:0 24px;font:16px/1.55 -apple-system,Helvetica,Arial,sans-serif;color:#1f2328}}
 pre,code{{font-family:ui-monospace,Menlo,monospace;font-size:13.5px}} pre{{background:#f6f8fa;padding:12px;overflow:auto;border-radius:6px}}
 code{{background:#f6f8fa;padding:1px 4px;border-radius:4px}} pre code{{background:none;padding:0}}
 table{{border-collapse:collapse;margin:12px 0}} th,td{{border:1px solid #d0d7de;padding:6px 10px;vertical-align:top}} th{{background:#f6f8fa}}
 h1,h2,h3{{border-bottom:1px solid #d8dee4;padding-bottom:4px;margin-top:1.6em}} .mermaid{{margin:16px 0;overflow:auto}}
 hr{{border:0;border-top:1px solid #d8dee4;margin:28px 0}} blockquote{{color:#57606a;border-left:4px solid #d0d7de;padding-left:12px}}
</style></head><body>
<div id="content"></div>
<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
const md = {json.dumps(md)};
const renderer = new marked.Renderer();
const codeFn = renderer.code.bind(renderer);
renderer.code = function(code, lang) {{
  const c = typeof code === "object" ? code.text : code; const l = typeof code === "object" ? code.lang : lang;
  if (l === "mermaid") return '<pre class="mermaid">' + c.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;") + '</pre>';
  return typeof code === "object" ? codeFn(code) : codeFn(code, lang);
}};
document.getElementById("content").innerHTML = marked.parse(md, {{renderer}});
mermaid.initialize({{startOnLoad:false, securityLevel:"loose"}});
await mermaid.run({{querySelector: ".mermaid"}});
</script></body></html>"""
out.write_text(page)
print(out)
subprocess.run(["open", str(out)])
