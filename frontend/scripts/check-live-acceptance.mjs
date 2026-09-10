#!/usr/bin/env node
import { access, readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";


const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const root = path.join(frontend, "e2e-live");
const forbidden = [
  ["Mock adapter import", /mockResearchAdapter/],
  ["request interception", /\b(?:page|context|browserContext)\.route\s*\(/],
  ["HAR replay", /\.routeFromHAR\s*\(/],
  ["subprocess escape", /(?:node:)?child_process|\b(?:exec|execFile|spawn|fork)(?:Sync)?\s*\(/],
  ["database client import", /(?:from\s+|require\s*\()["'](?:sqlalchemy|psycopg|pg|mysql2?|sqlite3|better-sqlite3|@prisma\/client|knex|sequelize)["']/i],
  ["direct SQL statement", /\b(?:select\s+[\s\S]{1,120}?\s+from|insert\s+into|update\s+[A-Za-z_][\w.]*\s+set|delete\s+from|alter\s+table|drop\s+(?:table|database)|create\s+(?:table|database))\b/i],
];

async function files(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  return (await Promise.all(entries.map(async (entry) => {
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? files(target) : [target];
  }))).flat();
}

async function existingModule(candidate) {
  for (const suffix of ["", ".ts", ".tsx", ".js", ".mjs", "/index.ts", "/index.tsx"]) {
    const resolved = `${candidate}${suffix}`;
    try {
      await access(resolved);
      return resolved;
    } catch {
      // Try the next TypeScript/JavaScript resolution candidate.
    }
  }
  return null;
}

const pending = [path.join(frontend, "playwright.live.config.ts"), ...(await files(root))];
const scanned = new Set();
const violations = [];
const allowedPackages = new Set(["@playwright/test", "node:path", "node:url"]);
while (pending.length) {
  const file = pending.pop();
  if (!file || scanned.has(file) || !/\.(?:ts|tsx|js|mjs)$/.test(file)) continue;
  scanned.add(file);
  const source = await readFile(file, "utf8");
  for (const [label, pattern] of forbidden) {
    if (pattern.test(source)) violations.push(`${path.relative(frontend, file)}: ${label}`);
  }
  const packageImports = source.matchAll(/(?:from\s+|import\s*\(|require\s*\()["']([^"']+)["']/g);
  for (const match of packageImports) {
    if (!match[1].startsWith(".") && !allowedPackages.has(match[1])) {
      violations.push(`${path.relative(frontend, file)}: unapproved package helper ${match[1]}`);
    }
  }
  const imports = source.matchAll(/(?:from\s+|import\s*\(|require\s*\()["'](\.[^"']+)["']/g);
  for (const match of imports) {
    const imported = await existingModule(path.resolve(path.dirname(file), match[1]));
    if (imported && !imported.startsWith(`${frontend}${path.sep}`)) {
      violations.push(`${path.relative(frontend, file)}: helper escapes frontend root ${match[1]}`);
    } else if (imported) {
      pending.push(imported);
    }
  }
}
if (violations.length) {
  throw new Error(`live acceptance contains forbidden shortcuts:\n${violations.join("\n")}`);
}
console.log("live acceptance static guard passed");
