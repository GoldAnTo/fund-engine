/**
 * 22bc46c8 224-file 浅度 health review.
 */
import { execSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const FILES = readFileSync("/tmp/files-22.txt", "utf8")
  .trim()
  .split("\n")
  .filter((f) => /\.(py|tsx?|jsx?)$/.test(f));

const PLACEHOLDER_PATTERNS = [
  /\bTODO\b/,
  /\bFIXME\b/,
  /\bXXX\b/,
  /\bHACK\b/,
  /^\s*pass\s*#?\s*(?:implement|placeholder|todo|fixme)/i,
  /\braise\s+NotImplementedError\b/,
  /\bstub\b.*\.\.\./,
  /^\s*#\s*stub\s*$/i,
  /\.\.\.\s*#\s*(?:todo|fixme|implement)/i,
];

const FINDINGS = [];
let totalLines = 0;
let totalHits = 0;
let emptyOrTrivial = 0;

for (const f of FILES) {
  let text;
  try {
    text = readFileSync(`/Users/xiongjiali/code/fund-engine/${f}`, "utf8");
  } catch (e) {
    FINDINGS.push({ file: f, kind: "read-error", detail: e.message });
    continue;
  }
  const lines = text.split("\n");
  const nonEmpty = lines.filter((l) => l.trim() && !l.trim().startsWith("#") && !l.trim().startsWith("//")).length;
  totalLines += lines.length;

  // 占位符命中
  const hits = [];
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    for (const pat of PLACEHOLDER_PATTERNS) {
      if (pat.test(ln)) {
        hits.push({ line: i + 1, snippet: ln.trim().slice(0, 120), pat: pat.source });
        break;
      }
    }
  }
  if (hits.length > 0) totalHits += hits.length;
  if (lines.length < 5 && nonEmpty < 2) emptyOrTrivial++;

  // Python: 简易 import 语法检查 (仅 ast 解析)
  if (f.endsWith(".py")) {
    try {
      // 用动态 import 不可行 (要依赖), 用 py_compile if available
      execSync(`python3 -c "import py_compile, sys; py_compile.compile('/Users/xiongjiali/code/fund-engine/${f}', doraise=True)"`, { stdio: "pipe" });
    } catch (e) {
      FINDINGS.push({ file: f, kind: "syntax-error", detail: e.stderr?.toString().slice(0, 400) || e.message.slice(0, 400) });
    }
  }

  // TypeScript: 收集 import 是否解析 (跳过 deep 检查, 只看 import 路径形态)
  if (f.endsWith(".ts") || f.endsWith(".tsx")) {
    const imports = [];
    for (const ln of lines) {
      const m = ln.match(/import\s+(?:[\w*\s,{}]+\s+from\s+)?["']([^"']+)["']/);
      if (m) imports.push(m[1]);
    }
    const suspicious = imports.filter((i) => i.startsWith("@/") || /^\.\.\//.test(i));
    if (imports.length > 0) {
      FINDINGS.push({ file: f, kind: "info", lineCount: lines.length, nonEmpty, imports: imports.length, suspicious: suspicious.length });
    } else {
      FINDINGS.push({ file: f, kind: "info", lineCount: lines.length, nonEmpty });
    }
  } else {
    FINDINGS.push({ file: f, kind: "info", lineCount: lines.length, nonEmpty });
  }

  if (hits.length > 0) {
    FINDINGS.push({ file: f, kind: "placeholder", hits });
  }
}

// 输出
console.log(`\n========= REVIEW SUMMARY (commit 22bc46c8) =========`);
console.log(`Total files scanned: ${FILES.length}`);
console.log(`Total line counts:  ${totalLines}`);
console.log(`Total placeholder hits: ${totalHits}`);
console.log(`Empty / trivial files: ${emptyOrTrivial}`);

const syntaxErrors = FINDINGS.filter((f) => f.kind === "syntax-error");
console.log(`\nSyntax errors (Python): ${syntaxErrors.length}`);
for (const e of syntaxErrors) {
  console.log(`  ✗ ${e.file}`);
  console.log(`    ${e.detail.split("\n").slice(0, 3).join("\n    ")}`);
}

const placeholders = FINDINGS.filter((f) => f.kind === "placeholder");
console.log(`\nFiles with placeholder/stub markers: ${placeholders.length}`);
for (const p of placeholders.slice(0, 25)) {
  console.log(`  ⚠ ${p.file}  (${p.hits.length} hits)`);
  for (const h of p.hits.slice(0, 3)) {
    console.log(`    L${h.line}: ${h.snippet}`);
  }
}
if (placeholders.length > 25) {
  console.log(`    ... and ${placeholders.length - 25} more files`);
}

const infos = FINDINGS.filter((f) => f.kind === "info");
const smallest = infos
  .filter((f) => !f.kind || f.kind === "info")
  .sort((a, b) => a.lineCount - b.lineCount)
  .slice(0, 10);
console.log(`\nSmallest 10 files (lineCount < 30 may be stubs):`);
for (const s of smallest) {
  console.log(`  �� ${s.file}  ${s.lineCount} lines (${s.nonEmpty} non-empty/comment)`);
}

// 输出 JSON 给后续脚本处理
const report = {
  scanned: FILES.length,
  totalLines,
  totalPlaceholderHits: totalHits,
  emptyOrTrivialCount: emptyOrTrivial,
  syntaxErrors,
  placeholders: placeholders.map((p) => ({ file: p.file, count: p.hits.length, samples: p.hits.slice(0, 3) })),
  smallest,
  fileInfos: infos,
};
writeFileSync("/tmp/review-22.json", JSON.stringify(report, null, 2));

console.log(`\nFull JSON report: /tmp/review-22.json`);
