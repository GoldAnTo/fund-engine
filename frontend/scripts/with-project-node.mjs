#!/usr/bin/env node
/** Run a frontend command with the Node version pinned in ../.nvmrc.
 *
 * Desktop shells occasionally expose an older system Node to npm even when
 * an nvm Node is active interactively.  The tiny launcher keeps dev, tests,
 * and Playwright on one runtime without hard-coding a user-specific path.
 */
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const scriptDir = resolve(fileURLToPath(new URL(".", import.meta.url)));
const target = readFileSync(resolve(scriptDir, "../../.nvmrc"), "utf8").trim();
const currentMajor = Number(process.versions.node.split(".")[0]);
const targetMajor = Number(target.replace(/^v/, "").split(".")[0]);
const args = process.argv.slice(2);
if (!args.length) throw new Error("expected a Node script to execute");

let executable = process.execPath;
if (currentMajor !== targetMajor) {
  const nvmRoot = process.env.NVM_DIR || resolve(homedir(), ".nvm");
  const versionsDir = resolve(nvmRoot, "versions", "node");
  const candidateVersion = existsSync(versionsDir)
    ? readdirSync(versionsDir).filter((name) => new RegExp(`^v${targetMajor}\\.\\d+\\.\\d+$`).test(name)).sort((a, b) => a.localeCompare(b, undefined, { numeric: true })).at(-1)
    : undefined;
  const candidate = candidateVersion
    ? resolve(versionsDir, candidateVersion, "bin", "node")
    : "";
  if (!existsSync(candidate)) {
    throw new Error(`Node ${targetMajor} is required; install it with: nvm install ${target}`);
  }
  executable = candidate;
}
const result = spawnSync(executable, args, { stdio: "inherit", env: process.env });
process.exit(result.status ?? 1);
