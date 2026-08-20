import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { dirname, extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import test, { after, before } from 'node:test';
import { chromium } from '../../frontend/node_modules/playwright/index.mjs';

const root = dirname(fileURLToPath(import.meta.url));
const mimeTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
};

let browser;
let origin;
let server;

before(async () => {
  server = createServer(async (request, response) => {
    const requestPath = new URL(request.url, 'http://local.test').pathname;
    const relativePath = normalize(requestPath === '/' ? 'index.html' : requestPath.slice(1));
    const filePath = join(root, relativePath);

    try {
      const fileStat = await stat(filePath);
      if (!fileStat.isFile() || !filePath.startsWith(root)) throw new Error('Not found');
      response.writeHead(200, { 'content-type': mimeTypes[extname(filePath)] ?? 'text/plain' });
      response.end(await readFile(filePath));
    } catch {
      response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
      response.end('Not found');
    }
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  origin = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({ headless: true });
});

after(async () => {
  await browser?.close();
  await new Promise((resolve) => server?.close(resolve));
});

test('search is the independent default route and teaches what can be searched', async () => {
  const page = await browser.newPage();
  await page.goto(`${origin}/?screen=unknown`);

  await assertPageText(page, ['公司投资研究', '我的研究', '从一家公司开始', 'GOOGL', '宁德时代', '云计算基础设施']);
  await assert.equal(await page.locator('body').getAttribute('data-prototype'), 'independent-investment-research');
  await assert.equal(await page.locator('aside').count(), 0);
  await assert.equal(await page.getByRole('searchbox', { name: '搜索股票、公司或行业' }).count(), 1);

  await page.close();
});

test('matching search reveals grouped, distinguishable entities', async () => {
  const page = await browser.newPage();
  await page.goto(origin);
  await page.getByRole('searchbox', { name: '搜索股票、公司或行业' }).fill('GOOGL');

  await assertPageText(page, [
    '证券',
    '公司',
    '行业',
    'GOOGL Class A',
    'GOOG Class C',
    'Alphabet Inc.',
    '云计算基础设施',
    'NASDAQ',
    'USD',
    '核心业务',
    '覆盖截止',
    '数据缺口',
    '代码匹配',
  ]);
  await assert.equal(await page.getByTestId('security-result').count(), 2);
  await assert.equal(await page.getByTestId('company-result').count(), 1);
  await assert.equal(await page.getByTestId('industry-result').count(), 1);

  await page.close();
});

test('selecting the Class A security opens setup with intentional defaults', async () => {
  const page = await browser.newPage();
  await page.goto(origin);
  await page.getByRole('searchbox', { name: '搜索股票、公司或行业' }).fill('Alphabet');
  await page.getByRole('link', { name: /GOOGL Class A/ }).click();
  await page.waitForURL(/screen=setup&security=GOOGL$/);

  await assertPageText(page, [
    'Alphabet',
    'GOOGL Class A',
    'NASDAQ',
    'USD',
    '核心业务',
    '1–2 年',
    '3–5 年',
    '5 年以上',
    '我已经有一些想法',
    '系统建议，待确认',
    '什么事实会证明核心业务增长无法转化为每股自由现金流？',
  ]);
  await assert.equal(
    await page.getByLabel('你想回答什么问题？').inputValue(),
    '以 3–5 年视角，这家公司靠什么创造价值，当前价格要求哪些假设成立？',
  );
  await assert.equal(await page.getByRole('radio', { name: '3–5 年', exact: true }).isChecked(), true);
  await assert.equal(await page.locator('details').getAttribute('open'), null);
  await page.locator('details summary').click();
  await assert.equal(await page.getByLabel('你的假设').count(), 1);

  await page.getByRole('button', { name: '建立研究' }).click();
  await page.waitForURL(/screen=workbench&security=GOOGL&variant=A$/);
  await assertPageText(page, ['研究工作台', 'Alphabet', 'GOOGL Class A']);

  await page.close();
});

test('new source and rendered routes keep the independent language boundary', async () => {
  const forbiddenTerms = [
    ['Research', 'Case'].join(''),
    ['Research', 'Run'].join(''),
    ['Event', ' Research'].join(''),
    ['证据', '图谱'].join(''),
    ['自动研究', '完成'].join(''),
    ['买', '入'].join(''),
    ['卖', '出'].join(''),
    ['目标', '价'].join(''),
    ['AI ', '置信度'].join(''),
    ['综合', '评分'].join(''),
  ];
  const sourceFiles = ['index.html', 'app.js', 'screens/search.js', 'screens/setup.js', 'screens/workbench.js', 'styles/tokens.css', 'styles/app.css', 'contract.test.mjs'];

  for (const relativePath of sourceFiles) {
    const source = await readFile(join(root, relativePath), 'utf8');
    for (const term of forbiddenTerms) assert.equal(source.includes(term), false, `${relativePath} contains forbidden copy`);
  }

  const page = await browser.newPage();
  for (const route of ['search', 'setup', 'workbench']) {
    await page.goto(`${origin}/?screen=${route}&security=GOOGL`);
    const text = await page.locator('body').innerText();
    for (const term of forbiddenTerms) assert.equal(text.includes(term), false, `${route} renders forbidden copy`);
  }
  await page.close();
});

async function assertPageText(page, fragments) {
  const text = await page.locator('body').innerText();
  for (const fragment of fragments) assert.match(text, new RegExp(escapeRegex(fragment)));
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
