# Research prototype harness

This directory is an isolated, code-native prototype. It does not call the production frontend, a backend, a database, or a live provider.

## Query routes

Open `/?screen=<id>` through the local capture server. Available route IDs are `overview`, `new-research`, `plan`, `case`, `graph`, `review`, `library`, `data`, and `versions`. Missing or unknown values resolve to `overview`.

The shell navigation uses the six stable product areas. Some routes intentionally remain labeled placeholders until their dedicated renderer task is implemented.

## Fixture boundary

`data.js` exposes one deterministic `window.PROTOTYPE_DATA` fixture for the AI-compute ResearchCase at cutoff `2025-06-30` and frozen snapshot `RS-2025-06-30-v3`. It is demonstration data, not a live research result. Point-in-time metadata and review state are explicit; AI-authored text remains labeled `AI 草案 · 未经人工复核`.

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

In Task 1 only `overview` is capture-ready; `--screens all` therefore means all capture-ready screens, not every placeholder route. Explicit requests for placeholder routes fail instead of producing misleading final images.

Every image capture uses the fixed viewport rather than a full-page screenshot. Capture fails before writing if document `scrollWidth` exceeds `1600` or `scrollHeight` exceeds `1000`; content is never silently clipped. The resulting PNG buffer is accepted only when its IHDR dimensions are exactly `1600 × 1000`.

Task 1 has no final screenshot mapping. Explicit capture commands write to a newly created OS temp directory and print the absolute output path. A later screen implementation may add a deliberate mapping to its named prototype PNG path after that screen is complete.

## Generated-artifact rule

Transient and test captures stay outside the repository in the OS temp directory, so `git add prototype/ui` cannot stage them. Final design PNGs are added only by the task that implements, verifies, and explicitly maps the corresponding screen renderer.
