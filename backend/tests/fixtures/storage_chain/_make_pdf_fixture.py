"""One-off generator for the frozen PDF fixture (committed, do not rerun).

Generates ``06_sungrow_annual_summary.pdf`` — a two-page simulated annual
report summary with a narrative page and a financial-table page.  The PDF is
committed to the repo and its content hash is frozen in
``docs/evaluation/dataset-manifest.json``; regenerating it would change the
hash and break the release gate, so only rerun when intentionally
re-freezing the fixture (and update the manifest accordingly).

Run with the managed Python runtime (reportlab + bundled Noto Sans SC):

    python backend/tests/fixtures/storage_chain/_make_pdf_fixture.py
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONT_PATH = (
    Path.home()
    / "Library/Application Support/kimi-desktop/daimon-share/daimon/runtime"
    / "python/fonts/NotoSansSC-Regular.ttf"
)
OUT = Path(__file__).with_name("06_sungrow_annual_summary.pdf")

TITLE = "阳光电源2025年年度报告摘要（节选）"

PAGE1_PARAS = [
    "2025年公司实现营业收入778.6亿元，同比增长43.5%；归母净利润110.4亿元，"
    "同比增长33.7%。光伏逆变器与储能系统双主业驱动，海外收入占比达58%。",
    "报告期内，公司储能系统收入298.5亿元，同比增长67.5%，全球大型储能市场"
    "份额持续提升，美国、欧洲、中东市场出货均创历史新高。",
]

TABLE_LINES = [
    "主要财务数据（合并口径）",
    "2025年 2024年",
    "单位：亿元",
    "营业收入 778.6 542.3",
    "研发费用 32.5 27.8",
    "归母净利润 110.4 82.6",
    "储能系统收入 298.5 178.2",
]

PAGE2_NOTE = "注：上表数据摘自公司2025年年度报告，审计意见为标准无保留意见。"


def main() -> None:
    pdfmetrics.registerFont(TTFont("NotoSansSC", str(FONT_PATH)))
    c = canvas.Canvas(str(OUT), pagesize=A4)
    width, height = A4
    margin, y = 56, height - 72

    c.setFont("NotoSansSC", 16)
    c.drawString(margin, y, TITLE)
    y -= 44

    c.setFont("NotoSansSC", 11)
    for para in PAGE1_PARAS:
        # naive wrap at 34 CJK chars per line
        for i in range(0, len(para), 34):
            c.drawString(margin, y, para[i : i + 34])
            y -= 20
        y -= 16

    c.showPage()
    y = height - 72
    c.setFont("NotoSansSC", 13)
    c.drawString(margin, y, TABLE_LINES[0])
    y -= 32
    c.setFont("NotoSansSC", 11)
    for line in TABLE_LINES[1:]:
        c.drawString(margin, y, line)
        y -= 22
    y -= 12
    c.setFont("NotoSansSC", 9)
    c.drawString(margin, y, PAGE2_NOTE)

    c.save()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
