"""一键复现：技术报告 Markdown → 带样式 HTML → PDF。

用法（仓库根目录）：
    python scripts/md2html_report.py

步骤：
1. 通过 `npx --yes marked --gfm` 将 Markdown 转为 HTML 片段；
2. 用内嵌 CSS（A4 版式、PingFang SC 中文字体、表格/代码样式）包装；
3. 使用系统 Chrome 的 headless PDF 命令打印，不依赖网页应用或其 npm 包。
   可通过 REPORT_CHROME 指定 Chrome/Chromium 可执行文件。

产物：
- docs/evidence-driven-research-report.html（中间产物，可删除）
- docs/evidence-driven-research-report.pdf（最终产物）
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "docs" / "evidence-driven-research-report.md"
HTML = ROOT / "docs" / "evidence-driven-research-report.html"
PDF = ROOT / "docs" / "evidence-driven-research-report.pdf"

CSS = """
@page { size: A4; margin: 20mm 18mm; }
* { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
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

def chrome_executable() -> str:
    configured = os.environ.get("REPORT_CHROME")
    candidates = [configured] if configured else [
        "google-chrome", "chromium", "chromium-browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if candidate and (resolved := shutil.which(candidate)):
            return resolved
    raise SystemExit("System Chrome is required; set REPORT_CHROME to its executable.")


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

    # Use an isolated browser profile; never borrow a user's running session.
    with tempfile.TemporaryDirectory(prefix="research-report-chrome-") as profile:
        run([
            chrome_executable(), "--headless", "--disable-gpu",
            f"--user-data-dir={profile}", "--no-pdf-header-footer",
            f"--print-to-pdf={PDF}", HTML.as_uri(),
        ])
    print(f"PDF written: {PDF}")


if __name__ == "__main__":
    sys.exit(main())
