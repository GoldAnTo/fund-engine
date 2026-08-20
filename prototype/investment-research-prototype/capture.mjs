import { spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdir, readFile, readdir, realpath, stat, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const outputDir = join(root, 'output');
const mimeTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
};

async function compatibleNode() {
  if (Number(process.versions.node.split('.')[0]) >= 20) return null;
  const versionsRoot = join(process.env.NVM_DIR ?? join(homedir(), '.nvm'), 'versions', 'node');
  const versions = await readdir(versionsRoot).catch(() => []);
  return versions
    .filter((version) => Number(version.replace(/^v/u, '').split('.')[0]) >= 20)
    .sort((left, right) => right.localeCompare(left, undefined, { numeric: true }))
    .map((version) => join(versionsRoot, version, 'bin', 'node'))[0];
}

async function reexecIfNeeded() {
  const node = await compatibleNode();
  if (node === null) return false;
  if (!node) throw new Error('Playwright capture requires an installed Node.js version 20 or newer.');
  const child = spawnSync(node, process.argv.slice(1), { stdio: 'inherit' });
  if (child.error) throw child.error;
  process.exitCode = child.status ?? 1;
  return true;
}

function insideRoot(candidate, base) {
  return candidate === base || candidate.startsWith(`${base}${sep}`);
}

async function startServer() {
  const servedRoot = await realpath(root);
  const server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url ?? '/', 'http://local.test').pathname);
      const candidate = resolve(servedRoot, pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, ''));
      if (!insideRoot(candidate, servedRoot)) {
        response.writeHead(403).end('Forbidden');
        return;
      }
      const resolved = await realpath(candidate);
      if (!insideRoot(resolved, servedRoot) || !(await stat(resolved)).isFile()) throw new Error('Not found');
      response.writeHead(200, {
        'cache-control': 'no-store',
        'content-type': mimeTypes[extname(resolved)] ?? 'application/octet-stream',
      });
      response.end(await readFile(resolved));
    } catch {
      response.writeHead(404).end('Not found');
    }
  });
  await new Promise((accept, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', accept);
  });
  const address = server.address();
  return {
    origin: `http://127.0.0.1:${address.port}`,
    close: () => new Promise((accept, reject) => server.close((error) => error ? reject(error) : accept())),
  };
}

function assertPng(png, viewport, name) {
  const signature = '89504e470d0a1a0a';
  if (png.length <= 24 || png.subarray(0, 8).toString('hex') !== signature) {
    throw new Error(`${name} is not a nonempty PNG`);
  }
  const width = png.readUInt32BE(16);
  const height = png.readUInt32BE(20);
  if (width !== viewport.width || height !== viewport.height) {
    throw new Error(`${name} is ${width}x${height}; expected ${viewport.width}x${viewport.height}`);
  }
}

async function capture(page, origin, { file, route, viewport, populateSearch = false }) {
  await page.setViewportSize(viewport);
  await page.goto(`${origin}/${route}`, { waitUntil: 'networkidle' });
  await page.evaluate(() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' }));
  if (populateSearch) {
    await page.getByRole('searchbox', { name: '搜索股票、公司或行业' }).fill('GOOGL');
    await page.getByTestId('security-result').first().waitFor();
  }
  const dimensions = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
    scrollY: window.scrollY,
  }));
  if (dimensions.documentWidth > dimensions.viewportWidth) {
    throw new Error(`${file} has document overflow: ${dimensions.documentWidth}px > ${dimensions.viewportWidth}px`);
  }
  if (dimensions.scrollY !== 0) throw new Error(`${file} capture did not start at the page top`);
  const png = await page.screenshot({ type: 'png', fullPage: false, animations: 'disabled' });
  assertPng(png, viewport, file);
  await writeFile(join(outputDir, file), png);
  process.stdout.write(`${file} ${viewport.width}x${viewport.height} width=${dimensions.documentWidth}px scrollY=${dimensions.scrollY} ${png.length} bytes\n`);
}

async function main() {
  if (await reexecIfNeeded()) return;
  const { chromium } = await import(new URL('../../frontend/node_modules/playwright/index.mjs', import.meta.url).href);
  await mkdir(outputDir, { recursive: true });
  const server = await startServer();
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  try {
    const desktop = { width: 1600, height: 1000 };
    await capture(page, server.origin, { file: '01-search.png', route: '?screen=search', viewport: desktop, populateSearch: true });
    await capture(page, server.origin, { file: '02-setup.png', route: '?screen=setup&security=GOOGL', viewport: desktop });
    await capture(page, server.origin, { file: '03-workbench-a.png', route: '?screen=workbench&security=GOOGL&variant=A', viewport: desktop });
    await capture(page, server.origin, { file: '04-workbench-b.png', route: '?screen=workbench&security=GOOGL&variant=B', viewport: desktop });
    await capture(page, server.origin, { file: '05-workbench-c.png', route: '?screen=workbench&security=GOOGL&variant=C', viewport: desktop });
    await capture(page, server.origin, { file: '06-workbench-mobile.png', route: '?screen=workbench&security=GOOGL&variant=A', viewport: { width: 390, height: 844 } });
  } finally {
    await browser.close();
    await server.close();
  }
}

await main();
