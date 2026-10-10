#!/usr/bin/env python3
"""Render an SVG to PNG with headless Chrome at its native size.
Usage: scripts/svg_to_png.py docs/images/system-overview.svg [width height]
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
svg = Path(sys.argv[1]).resolve()
text = svg.read_text()
if len(sys.argv) >= 4:
    w, h = int(sys.argv[2]), int(sys.argv[3])
else:
    mw, mh = re.search(r'width="(\d+)"', text), re.search(r'height="(\d+)"', text)
    if not (mw and mh):
        sys.exit("SVG has no width/height; pass them explicitly")
    w, h = int(mw.group(1)), int(mh.group(1))
out = svg.with_suffix(".png")
with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
    f.write(f'<!doctype html><html><body style="margin:0;background:#fff">'
            f'<img src="file://{svg}" width="{w}" height="{h}" style="display:block"></body></html>')
    page = f.name
subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                "--force-device-scale-factor=1", f"--window-size={w},{h}",
                "--allow-file-access-from-files", f"--screenshot={out}", f"file://{page}"],
               check=True, capture_output=True)
print(out, f"{w}x{h}")
