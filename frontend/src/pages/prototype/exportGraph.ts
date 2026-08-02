/**
 * Wiki 图谱导出工具：把画布 SVG 连同计算后的样式一起序列化，
 * 渲染到离屏 canvas 后导出 PNG 或多页 PDF（不依赖第三方库）。
 * 中文文本页通过 SVG foreignObject 由浏览器排版后栅格化，再嵌入 PDF。
 */

import type { RelationshipGraphView } from "../../domain/prototypeTypes";

const GRAPH_VARS = [
  "--paper",
  "--paper-soft",
  "--ink",
  "--ink-soft",
  "--ink-muted",
  "--rule",
  "--rule-strong",
  "--support",
  "--contradict",
  "--warning",
  "--reviewed",
  "--ai-draft",
  "--provider-accent",
  "--gap-accent",
];

/** 收集样式表里所有作用于 wiki-* 类的规则（SVG 样式都在外部 CSS 中）。 */
function collectGraphCss(): string {
  let css = "";
  for (const sheet of Array.from(document.styleSheets)) {
    let rules: CSSRuleList;
    try {
      rules = sheet.cssRules;
    } catch {
      continue; // 跨域样式表跳过
    }
    for (const rule of Array.from(rules)) {
      if (rule.cssText.includes("wiki-")) css += `${rule.cssText}\n`;
    }
  }
  return css;
}

/** 克隆 SVG 并注入 CSS 变量与 wiki-* 样式，使其脱离页面也能完整渲染。 */
export function serializeGraphSvg(svg: SVGSVGElement): string {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  const rootStyle = getComputedStyle(document.documentElement);
  const vars = GRAPH_VARS.map(
    (name) => `${name}:${rootStyle.getPropertyValue(name)};`,
  ).join("");
  const style = document.createElementNS("http://www.w3.org/2000/svg", "style");
  style.textContent =
    `svg{${vars}}\n` +
    collectGraphCss() +
    // 页面里 font-family 依赖 inherit，导出时显式指定
    `\ntext{font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;}`;
  clone.insertBefore(style, clone.firstChild);
  return new XMLSerializer().serializeToString(clone);
}

function imageToCanvas(
  img: HTMLImageElement,
  w: number,
  h: number,
): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, w, h);
  ctx.drawImage(img, 0, 0, w, h);
  return canvas;
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("图谱渲染失败"));
    };
    img.src = url;
  });
}

function renderSvgTextToCanvas(svgText: string): Promise<HTMLCanvasElement> {
  // viewBox 决定位图尺寸；按 2x 导出保证清晰度
  const match = svgText.match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/);
  const w = match ? Number(match[1]) : 1200;
  const h = match ? Number(match[2]) : 600;
  const scale = 2;
  const url = URL.createObjectURL(
    new Blob([svgText], { type: "image/svg+xml;charset=utf-8" }),
  );
  return loadImage(url).then((img) => {
    const canvas = imageToCanvas(img, w * scale, h * scale);
    URL.revokeObjectURL(url);
    return canvas;
  });
}

function triggerDownload(url: string, filename: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
}

export async function exportGraphPng(svg: SVGSVGElement, filename: string) {
  const canvas = await renderSvgTextToCanvas(serializeGraphSvg(svg));
  triggerDownload(canvas.toDataURL("image/png"), `${filename}.png`);
}

// ── 最小多页 PDF 生成：逐页嵌入一张 JPEG ──────────────────────────────

interface PdfPageImage {
  jpeg: Uint8Array;
  /** 位图像素尺寸 */
  imgW: number;
  imgH: number;
  /** 页面尺寸（pt） */
  pageW: number;
  pageH: number;
}

type PdfChunk = string | Uint8Array;

const A4_PORTRAIT = { w: 595, h: 842 };
const A4_LANDSCAPE = { w: 842, h: 595 };

function buildPdf(pages: PdfPageImage[]): Uint8Array {
  const chunks: PdfChunk[] = ["%PDF-1.4\n"];
  let size = (chunks[0] as string).length;
  const offsets: number[] = [];
  const pushObject = (body: PdfChunk[]) => {
    offsets.push(size);
    const head = `${offsets.length} 0 obj\n`;
    chunks.push(head, ...body, "\nendobj\n");
    size +=
      head.length +
      9 +
      body.reduce((acc, c) => acc + c.length, 0);
  };

  // 对象编号：1=Catalog 2=Pages，之后每页 3 个对象（Page/Image/Contents）
  const pageCount = pages.length;
  const pageObjId = (i: number) => 3 + i * 3;
  const kids = pages.map((_, i) => `${pageObjId(i)} 0 R`).join(" ");

  pushObject(["<< /Type /Catalog /Pages 2 0 R >>"]);
  pushObject([`<< /Type /Pages /Kids [${kids}] /Count ${pageCount} >>`]);

  const margin = 28;
  pages.forEach((page, i) => {
    const fit = Math.min(
      (page.pageW - margin * 2) / page.imgW,
      (page.pageH - margin * 2) / page.imgH,
    );
    const w = page.imgW * fit;
    const h = page.imgH * fit;
    const x = (page.pageW - w) / 2;
    const y = (page.pageH - h) / 2;
    const imgId = pageObjId(i) + 1;
    const contentId = pageObjId(i) + 2;
    pushObject([
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${page.pageW} ${page.pageH}] ` +
        `/Resources << /XObject << /Im0 ${imgId} 0 R >> >> /Contents ${contentId} 0 R >>`,
    ]);
    pushObject([
      `<< /Type /XObject /Subtype /Image /Width ${page.imgW} /Height ${page.imgH} ` +
        `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode ` +
        `/Length ${page.jpeg.length} >>\nstream\n`,
      page.jpeg,
      "\nendstream",
    ]);
    const content = `q ${w.toFixed(2)} 0 0 ${h.toFixed(2)} ${x.toFixed(2)} ${y.toFixed(2)} cm /Im0 Do Q`;
    pushObject([
      `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
    ]);
  });

  const xrefPos = size;
  const total = offsets.length + 1;
  let xref = `xref\n0 ${total}\n0000000000 65535 f \n`;
  for (const off of offsets) {
    xref += `${String(off).padStart(10, "0")} 00000 n \n`;
  }
  xref += `trailer\n<< /Size ${total} /Root 1 0 R >>\nstartxref\n${xrefPos}\n%%EOF`;
  chunks.push(xref);

  const byteTotal = chunks.reduce((acc, c) => acc + c.length, 0);
  const out = new Uint8Array(byteTotal);
  const encoder = new TextEncoder();
  let pos = 0;
  for (const c of chunks) {
    const bytes = typeof c === "string" ? encoder.encode(c) : c;
    out.set(bytes, pos);
    pos += bytes.length;
  }
  return out;
}

function base64ToBytes(base64: string): Uint8Array {
  const bin = atob(base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function canvasToJpeg(canvas: HTMLCanvasElement): Uint8Array {
  return base64ToBytes(canvas.toDataURL("image/jpeg", 0.92).split(",")[1]);
}

function downloadPdf(pages: PdfPageImage[], filename: string) {
  const pdf = buildPdf(pages);
  const url = URL.createObjectURL(
    new Blob([pdf.buffer as ArrayBuffer], { type: "application/pdf" }),
  );
  triggerDownload(url, `${filename}.pdf`);
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export async function exportGraphPdf(svg: SVGSVGElement, filename: string) {
  const canvas = await renderSvgTextToCanvas(serializeGraphSvg(svg));
  downloadPdf(
    [
      {
        jpeg: canvasToJpeg(canvas),
        imgW: canvas.width,
        imgH: canvas.height,
        ...{ pageW: A4_LANDSCAPE.w, pageH: A4_LANDSCAPE.h },
      },
    ],
    filename,
  );
}

// ── 研究简报：图谱页 + 证据清单与出处附录 ─────────────────────────────
// 附录页不用 foreignObject（该浏览器构建会因此污染 canvas），
// 直接用 Canvas 2D 排版中文表格并按 A4 竖版分页。

const PAGE_W = 1190; // A4 竖版 2x
const PAGE_H = 1684;
const MARGIN = 56;
const CONTENT_W = PAGE_W - MARGIN * 2;
const FONT = `"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif`;
const INK = "#242424";
const INK_SOFT = "#666666";
const INK_MUTED = "#8a8a8a";
const RULE = "#ddd6c9";
const HEADER_BG = "#f2efe9";
const ACCENT = "#4a6fa5";
const SUPPORT = "#2f6b46";
const CONTRADICT = "#a33b32";

class BriefWriter {
  readonly pages: HTMLCanvasElement[] = [];
  private ctx!: CanvasRenderingContext2D;
  private y = 0;

  constructor() {
    this.newPage();
  }

  private newPage() {
    const canvas = document.createElement("canvas");
    canvas.width = PAGE_W;
    canvas.height = PAGE_H;
    this.ctx = canvas.getContext("2d")!;
    this.ctx.fillStyle = "#ffffff";
    this.ctx.fillRect(0, 0, PAGE_W, PAGE_H);
    this.ctx.textBaseline = "top";
    this.pages.push(canvas);
    this.y = MARGIN;
  }

  private ensure(height: number) {
    if (this.y + height > PAGE_H - MARGIN) this.newPage();
  }

  private wrap(text: string, maxWidth: number): string[] {
    const lines: string[] = [];
    let line = "";
    for (const ch of text) {
      if (ch === "\n" || this.ctx.measureText(line + ch).width > maxWidth) {
        if (line) lines.push(line);
        line = ch === "\n" ? "" : ch;
      } else {
        line += ch;
      }
    }
    if (line) lines.push(line);
    return lines.length ? lines : [""];
  }

  /** 返回占用的总高度。 */
  private text(
    content: string,
    opts: {
      size: number;
      weight?: number;
      color?: string;
      lineHeight?: number;
      x?: number;
      maxWidth?: number;
    },
  ): number {
    const { size, weight = 400, color = INK, x = MARGIN } = opts;
    const lineHeight = opts.lineHeight ?? Math.round(size * 1.55);
    const maxWidth = opts.maxWidth ?? CONTENT_W - (x - MARGIN);
    this.ctx.font = `${weight} ${size}px ${FONT}`;
    this.ctx.fillStyle = color;
    const lines = this.wrap(content, maxWidth);
    lines.forEach((line, i) => {
      this.ctx.fillText(line, x, this.y + i * lineHeight);
    });
    return lines.length * lineHeight;
  }

  title(content: string) {
    this.ensure(60);
    this.y += this.text(content, { size: 34, weight: 600 }) + 10;
  }

  lede(content: string) {
    this.ensure(40);
    this.y += this.text(content, { size: 17, color: INK_SOFT }) + 8;
  }

  basis(content: string) {
    this.ensure(30);
    this.y += this.text(content, { size: 14, color: INK_MUTED }) + 22;
  }

  sectionHead(content: string) {
    this.ensure(64);
    this.y += 18;
    this.ctx.fillStyle = ACCENT;
    this.ctx.fillRect(MARGIN, this.y + 2, 5, 26);
    this.y += this.text(content, {
      size: 20,
      weight: 600,
      x: MARGIN + 14,
    });
    this.y += 10;
  }

  sectionMeta(content: string) {
    this.ensure(30);
    this.y += this.text(content, { size: 14, color: INK_MUTED }) + 8;
  }

  empty(content: string) {
    this.ensure(30);
    this.y += this.text(content, { size: 14, color: INK_MUTED }) + 6;
  }

  table(
    cols: { label: string; width: number }[],
    rows: { text: string; color?: string; weight?: number }[][],
  ) {
    if (rows.length === 0) return;
    const pad = 9;
    const fontSize = 13.5;
    const lineHeight = 20;

    const rowHeight = (cells: string[]) => {
      this.ctx.font = `400 ${fontSize}px ${FONT}`;
      const maxLines = Math.max(
        ...cells.map((cell, i) => this.wrap(cell, cols[i].width - pad * 2).length),
      );
      return maxLines * lineHeight + pad * 2;
    };
    const drawRow = (
      cells: { text: string; color?: string; weight?: number }[],
      isHeader: boolean,
    ) => {
      const h = rowHeight(cells.map((c) => c.text));
      this.ensure(h);
      let x = MARGIN;
      if (isHeader) {
        this.ctx.fillStyle = HEADER_BG;
        this.ctx.fillRect(MARGIN, this.y, CONTENT_W, h);
      }
      cells.forEach((cell, i) => {
        this.ctx.strokeStyle = RULE;
        this.ctx.lineWidth = 1;
        this.ctx.strokeRect(x, this.y, cols[i].width, h);
        this.ctx.font = `${cell.weight ?? (isHeader ? 600 : 400)} ${fontSize}px ${FONT}`;
        this.ctx.fillStyle = cell.color ?? INK;
        const lines = this.wrap(cell.text, cols[i].width - pad * 2);
        lines.forEach((line, li) => {
          this.ctx.fillText(line, x + pad, this.y + pad + li * lineHeight);
        });
        x += cols[i].width;
      });
      this.y += h;
    };

    const headerCells = cols.map((c) => ({ text: c.label }));
    // 表头 + 首行尽量放在同一页
    this.ensure(rowHeight(cols.map((c) => c.label)) + rowHeight(rows[0].map((c) => c.text)) + 4);
    drawRow(headerCells, true);
    rows.forEach((row) => {
      // 跨页时重复表头
      const h = rowHeight(row.map((c) => c.text));
      if (this.y + h > PAGE_H - MARGIN) {
        this.newPage();
        drawRow(headerCells, true);
      }
      drawRow(row, false);
    });
    this.y += 8;
  }
}

/** 组装简报附录页：按命题分组的证据清单 + 因果链 + 投影节点。 */
function buildBriefPages(
  view: RelationshipGraphView,
  selectedId: string,
): HTMLCanvasElement[] {
  const writer = new BriefWriter();
  const layerOf = (key: string) =>
    view.layers.find((l) => l.key === key)?.nodes ?? [];

  writer.title(`研究简报 · ${view.case.title || "证据图谱"}`);
  if (view.case.question) writer.lede(view.case.question);
  writer.basis(
    `证据截止：${view.case.cutoff || "—"}` +
      (view.case.snapshotId ? ` · 冻结快照：${view.case.snapshotId}` : "") +
      ` · 生成时间：${new Date().toLocaleString("zh-CN")}`,
  );

  const evidenceCols = [
    { label: "关系", width: 90 },
    { label: "来源事实", width: 388 },
    { label: "审核", width: 150 },
    { label: "出处", width: 230 },
    { label: "发表", width: 110 },
    { label: "截止", width: 110 },
  ];

  for (const thesis of layerOf("thesis")) {
    const focused = thesis.id === selectedId;
    writer.sectionHead(`${focused ? "★ 聚焦命题" : "命题"} · ${thesis.title}`);
    writer.sectionMeta(`${thesis.meta} · ${thesis.review}`);
    const rows = view.edges
      .filter(
        (e) =>
          e.kind === "evidence" &&
          (e.source === thesis.id || e.target === thesis.id),
      )
      .flatMap((e) => {
        const otherId = e.source === thesis.id ? e.target : e.source;
        const node = view.nodes.find((n) => n.id === otherId);
        if (!node) return [];
        const contradict = e.role?.includes("contradict");
        return [
          [
            {
              text: e.label,
              color: contradict ? CONTRADICT : SUPPORT,
              weight: 600,
            },
            { text: node.title },
            { text: node.review },
            { text: node.sourceSpan },
            { text: node.publicationDate },
            { text: node.asOf },
          ],
        ];
      });
    if (rows.length === 0) {
      writer.empty("尚无证据关系。");
    } else {
      writer.table(evidenceCols, rows);
    }
  }

  const causals = layerOf("causal");
  if (causals.length > 0) {
    writer.sectionHead("因果链");
    writer.table(
      [
        { label: "次序", width: 140 },
        { label: "步骤", width: 588 },
        { label: "审核", width: 350 },
      ],
      causals.map((n) => [
        { text: n.meta },
        { text: n.title },
        { text: n.review },
      ]),
    );
  }

  const companies = layerOf("company");
  if (companies.length > 0) {
    writer.sectionHead("相关公司");
    writer.table(
      [
        { label: "主体", width: 300 },
        { label: "角色", width: 300 },
        { label: "范围", width: 478 },
      ],
      companies.map((n) => [
        { text: n.title },
        { text: n.meta },
        { text: n.scope },
      ]),
    );
  }

  const funds = layerOf("fund");
  if (funds.length > 0) {
    writer.sectionHead("基金投影");
    writer.table(
      [
        { label: "基金", width: 340 },
        { label: "持仓口径", width: 400 },
        { label: "审核", width: 338 },
      ],
      funds.map((n) => [
        { text: n.title },
        { text: n.meta },
        { text: n.review },
      ]),
    );
  }

  return writer.pages;
}

/** 导出研究简报 PDF：第 1 页完整图谱（横版），附录为证据清单与出处。 */
export async function exportResearchBrief(
  svg: SVGSVGElement,
  view: RelationshipGraphView,
  selectedId: string,
  filename: string,
) {
  const graphCanvas = await renderSvgTextToCanvas(serializeGraphSvg(svg));
  const briefPages = buildBriefPages(view, selectedId);
  downloadPdf(
    [
      {
        jpeg: canvasToJpeg(graphCanvas),
        imgW: graphCanvas.width,
        imgH: graphCanvas.height,
        pageW: A4_LANDSCAPE.w,
        pageH: A4_LANDSCAPE.h,
      },
      ...briefPages.map((canvas) => ({
        jpeg: canvasToJpeg(canvas),
        imgW: canvas.width,
        imgH: canvas.height,
        pageW: A4_PORTRAIT.w,
        pageH: A4_PORTRAIT.h,
      })),
    ],
    filename,
  );
}
