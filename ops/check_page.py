#!/usr/bin/env python3
"""Check the syntax of the page module. It does not check a loose script."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    html = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
    marker = '<script type="module">'
    start = html.find(marker)
    if start < 0:
        print("no module")
        return 1
    start = html.find(">", start) + 1
    end = html.find("</script>", start)
    if end < 0:
        print("module has no closing tag")
        return 1
    path = Path(tempfile.gettempdir()) / "visor-page.mjs"
    path.write_text(html[start:end], encoding="utf-8")
    return subprocess.run(["node", "--check", str(path)]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
