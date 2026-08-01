# Research prototype harness

This directory is an isolated, code-native prototype. It does not call the production frontend, a backend, a database, or a live provider.

## Query routes

Open `/?screen=<id>` through the local capture server. Available route IDs are `overview`, `new-research`, `plan`, `case`, `graph`, `review`, `library`, `data`, and `versions`. Missing or unknown values resolve to `overview`.

The shell navigation uses the six stable product areas. Some routes intentionally remain labeled placeholders until their dedicated renderer task is implemented.

## Fixture boundary

`data.js` exposes one deterministic `window.PROTOTYPE_DATA` fixture for the AI-compute ResearchCase at cutoff `2025-06-30` and frozen snapshot `RS-2025-06-30-v3`. It is demonstration data, not a live research result. Point-in-time metadata and review state are explicit; AI-authored text remains labeled `AI 草案 · 未经人工复核`.

`new-research-state.js` owns the new-research draft contract. Confirmed drafts use session schema `v2` and the `new-research-confirmation:v2:<caseId>` key; earlier keys cannot unlock a later step. Title and body limits are exported by the state module and mirrored by the visible form controls. Textarea height is recalculated after content and responsive-width changes so the fixed desktop capture does not hide required draft text.

## Runtime and install preflight

Playwright requires Node 20 or newer. When the shell command starts under an older Node release, the harness re-executes through an installed compatible NVM runtime when one is present; otherwise it reports the runtime requirement without modifying dependencies.

The harness imports the repository's Playwright dependency from `frontend/node_modules`. If that dependency is unavailable, run exactly:

```sh
cd frontend && npm ci
```

No package files are owned by this prototype.

Browser launch first uses Playwright's bundled Chromium. If that executable is absent, capture falls back to system Google Chrome. If neither browser runtime is available, install bundled Chromium with:

```sh
cd frontend && npx playwright install chromium
```

Installing system Google Chrome is the alternative fallback. Browser-runtime failures do not suggest `npm ci`, because reinstalling the JavaScript dependency does not guarantee a browser executable.

## Test and capture

From the repository root:

```sh
node prototype/ui/contract.test.mjs --screens shell
node prototype/ui/contract.test.mjs --screens all
node prototype/ui/capture.mjs --screens shell
node prototype/ui/capture.mjs --screens overview
```

`--screens shell` verifies the 1600 × 1000, DPR 1 shell without writing an image. The test command accepts `shell`, `all`, or a comma-separated list of route IDs.

`--screens overview` atomically updates the implemented screen's final capture at `prototype/设计原型1.png`. `--screens all` means all capture-ready screens, not every routed placeholder. Unimplemented screens are rejected instead of producing misleading final images.

Every image capture uses the fixed viewport rather than a full-page screenshot. Capture fails before writing if document `scrollWidth` exceeds `1600` or `scrollHeight` exceeds `1000`; content is never silently clipped. The resulting PNG buffer is accepted only when its IHDR dimensions are exactly `1600 × 1000`.

Before replacing a mapped final image, capture parses every PNG chunk to exact EOF, checks each chunk's CRC32, requires the complete IHDR/IDAT/IEND structure, and accepts only Playwright's RGB8, color-type 2, non-interlaced profile. IDAT zlib decompression is bounded to one byte above the expected scanline length, after which capture requires the exact decoded length and valid filter bytes. It then writes a validated temporary sibling and uses an atomic rename over the final target; a failed validation, write, or rename leaves the existing final unchanged and removes the temporary file.

## Generated-artifact rule

Final design PNGs are written only for implemented, verified renderers with an explicit registry mapping. Contract-test fixtures use the operating system's temporary directory and are cleaned after each assertion.
