# Research prototype harness

This directory is an isolated, code-native prototype. It does not call the production frontend, a backend, a database, or a live provider.

## Query routes

Open `/?screen=<id>` through the local capture server. Available route IDs are `overview`, `new-research`, `plan`, `case`, `graph`, `review`, `library`, `data`, and `versions`. Missing or unknown values resolve to `overview`.

The shell navigation uses the six stable product areas. Some routes intentionally remain labeled placeholders until their dedicated renderer task is implemented.

## Fixture boundary

`data.js` exposes one deterministic `window.PROTOTYPE_DATA` fixture for the AI-compute ResearchCase at cutoff `2025-06-30` and frozen snapshot `RS-2025-06-30-v3`. It is demonstration data, not a live research result. Point-in-time metadata and review state are explicit; AI-authored text remains labeled `AI 草案 · 未经人工复核`.

## Install preflight

The harness uses the repository's existing Playwright installation. If it is unavailable, run exactly:

```sh
cd frontend && npm ci
```

No package files are owned by this prototype.

Playwright requires Node 20 or newer. When the shell command starts under an older Node release, the harness re-executes through an installed compatible NVM runtime when one is present; otherwise it reports the runtime requirement without modifying dependencies.

## Test and capture

From the repository root:

```sh
node prototype/ui/contract.test.mjs --screens shell
node prototype/ui/contract.test.mjs --screens all
node prototype/ui/capture.mjs --screens shell
node prototype/ui/capture.mjs --screens overview
```

`--screens shell` verifies the 1600 × 1000, DPR 1 shell without writing an image. The test command accepts `shell`, `all`, or a comma-separated list of route IDs.

Capture output maps each capture-ready route to `prototype/ui/generated/<screen>.png`. In Task 1 only `overview` is capture-ready; `--screens all` therefore means all capture-ready screens, not every placeholder route. Explicit requests for placeholder routes fail instead of producing misleading final images.

## Generated-artifact rule

Files under `prototype/ui/generated/` are reproducible local artifacts and must not be committed. Final design PNGs are added only by the task that implements and verifies the corresponding screen renderer.
