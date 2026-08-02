"""一键复现：技术报告 Markdown → 带样式 HTML → PDF。

用法（仓库根目录）：
    python scripts/md2html_report.py

步骤：
1. 通过 `npx --yes marked --gfm` 将 Markdown 转为 HTML 片段；
2. 用内嵌 CSS（A4 版式、PingFang SC 中文字体、表格/代码样式）包装；
3. 在 frontend/ 下临时生成 Playwright 脚本，用系统 Chrome
   （channel="chrome"，兼容 macOS 12）以 printBackground 打印 PDF。

产物：
- docs/evidence-driven-research-report.html（中间产物，可删除）
- docs/evidence-driven-research-report.pdf（最终产物）
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "docs" / "evidence-driven-research-report.md"
HTML = ROOT / "docs" / "evidence-driven-research-report.html"
PDF = ROOT / "docs" / "evidence-driven-research-report.pdf"
FRONTEND = ROOT / "frontend"

CSS = """
@page { size: A4; margin: 20mm 18mm; }
* { box-sizing: border-box; }
html, body { background: #ffffff; }
body {
  font-family: "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", sans-serif;
  font-size: 10.5pt; line-height: 1.75; color: #1a2330; margin: 0;
}
h1 {
  font-size: 19pt; line-height: 1.35; color: #0f1c2e;
  border-bottom: 2.5px solid #2f5d8a; padding-bottom: 10px; margin: 0 0 6px;
}
h2 {
  font-size: 13.5pt; color: #17335c; margin: 26px 0 10px;
  border-left: 4px solid #2f5d8a; padding-left: 10px;
  page-break-after: avoid;
}
h3 { font-size: 11.5pt; color: #24466f; margin: 18px 0 8px; page-break-after: avoid; }
p { margin: 7px 0; }
strong { color: #10243e; }
table {
  border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 9.5pt;
  page-break-inside: avoid;
}
th, td { border: 1px solid #c9d4e0; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: #eef3f9; color: #17335c; font-weight: 600; }
tr:nth-child(even) td { background: #f8fafc; }
code {
  font-family: "SF Mono", Menlo, monospace; font-size: 8.8pt;
  background: #f2f5f8; padding: 1px 4px; border-radius: 3px; color: #243b55;
}
pre {
  background: #f6f8fa; border: 1px solid #dde4ec; border-radius: 6px;
  padding: 12px 14px; overflow-x: auto; page-break-inside: avoid;
}
pre code { background: none; padding: 0; font-size: 8.5pt; line-height: 1.5; }
ol, ul { padding-left: 22px; margin: 7px 0; }
li { margin: 3px 0; }
blockquote {
  margin: 10px 0; padding: 6px 14px; border-left: 3px solid #b7c6d8;
  background: #f7f9fb; color: #44576d;
}
hr { border: none; border-top: 1px solid #d5dee8; margin: 20px 0; }
a { color: #2f5d8a; text-decoration: none; }
em { color: #44576d; }
"""

PRINT_JS = """
import { chromium } from "@playwright/test";
const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage();
await page.goto("file://%(html)s");
await page.pdf({
  path: "%(pdf)s",
  format: "A4",
  printBackground: true,
  margin: { top: "20mm", bottom: "20mm", left: "18mm", right: "18mm" },
});
await browser.close();
console.log("PDF printed");
"""


def run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> None:
    # 1. Markdown → HTML 片段
    body = subprocess.run(
        ["npx", "--yes", "marked", "--gfm", str(MD)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    # 2. 包装完整 HTML
    HTML.write_text(
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>证据驱动的行业研究系统：方法论与验证结果</title>"
        f"<style>{CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )
    print(f"HTML written: {HTML}")

    # 3. Playwright（系统 Chrome）打印 PDF
    js = PRINT_JS % {"html": HTML, "pdf": PDF}
    with tempfile.NamedTemporaryFile(
        "w", suffix=".tmp.mjs", dir=FRONTEND, delete=False, encoding="utf-8"
    ) as f:
        f.write(js)
        tmp = Path(f.name)
    try:
        run(["node", str(tmp.name)], cwd=FRONTEND)
    finally:
        tmp.unlink(missing_ok=True)
    print(f"PDF written: {PDF}")


if __name__ == "__main__":
    sys.exit(main())
