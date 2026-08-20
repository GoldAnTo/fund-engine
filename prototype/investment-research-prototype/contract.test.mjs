import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { readFile, readdir, stat } from 'node:fs/promises';
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

test('the Alphabet company result requires an explicit A or C security choice', async () => {
  const page = await browser.newPage();
  await page.goto(origin);
  await page.getByRole('searchbox', { name: '搜索股票、公司或行业' }).fill('Alphabet');

  const company = page.getByTestId('company-result');
  await assert.equal(await company.getAttribute('href'), null);
  await company.locator('summary').click();
  await assertPageText(page, ['请选择具体证券', 'GOOGL Class A', 'GOOG Class C', '投票权类别']);
  await company.getByRole('link', { name: /GOOG Class C/ }).click();
  await assert.equal(new URL(page.url()).searchParams.get('security'), 'GOOG');
  await assertPageText(page, ['Alphabet', 'GOOG Class C']);
  await page.close();
});

test('the CATL example preserves the company-to-security mapping through setup and workbench', async () => {
  const page = await browser.newPage();
  await page.goto(origin);
  await page.getByRole('button', { name: '宁德时代', exact: true }).click();

  await assert.equal(await page.getByRole('searchbox', { name: '搜索股票、公司或行业' }).inputValue(), '宁德时代');
  await assertPageText(page, [
    '证券',
    '公司',
    '300750.SZ',
    'CATL',
    '宁德时代新能源科技股份有限公司',
    '证券 · 名称匹配',
    '公司 · 关联实体',
    '深圳证券交易所',
    'CNY',
    '动力电池、储能电池与电池材料',
    '覆盖截止',
    '数据缺口',
    '公司对应证券',
  ]);
  await assert.equal(await page.getByTestId('catl-security-result').count(), 1);
  await assert.equal(await page.getByTestId('catl-company-result').count(), 1);
  await page.getByTestId('catl-security-result').click();
  await assertPageText(page, [
    '宁德时代', '300750.SZ', '深圳证券交易所', 'CNY', '公司对应证券',
    '动力电池需求增长能否抵消单位价格下降，并转化为可持续自由现金流？',
    '储能业务的增长与利润贡献如何验证？',
    '海外产能利用率何时能够覆盖新增折旧与资本成本？',
  ]);
  const catlSetup = await page.locator('main').innerText();
  for (const alphabetOnly of ['用户注意力', '搜索分发', '云服务与 AI']) assert.equal(catlSetup.includes(alphabetOnly), false);
  await page.locator('details summary').click();
  const catlHypothesisPlaceholder = await page.getByLabel('你的假设').getAttribute('placeholder');
  assert.match(catlHypothesisPlaceholder, /动力电池|储能/u);
  for (const alphabetOnly of ['Cloud', 'AI 基础设施', '云业务']) assert.equal(catlHypothesisPlaceholder.includes(alphabetOnly), false);
  await page.getByRole('button', { name: '建立研究' }).click();
  await assertPageText(page, ['研究工作台', '宁德时代', '300750.SZ', '动力电池与储能系统', '上游材料议价与海外本地化']);
  const catlWorkbench = await page.locator('main').innerText();
  assert.equal(catlWorkbench.includes('Alphabet'), false);
  assert.equal(catlWorkbench.includes('搜索广告'), false);

  await page.close();
});

test('selecting an industry result opens an industry-first representative-company setup', async () => {
  const page = await browser.newPage();
  await page.goto(origin);
  await page.getByRole('button', { name: '云计算基础设施', exact: true }).click();
  await page.getByRole('link', { name: /云计算基础设施/ }).click();

  const url = new URL(page.url());
  assert.equal(url.searchParams.get('industry'), 'cloud-infrastructure');
  await assertPageText(page, [
    '行业入口',
    '云计算基础设施',
    '先从代表公司验证行业判断',
    '代表公司',
    'Alphabet',
    'GOOGL Class A',
  ]);
  await page.getByRole('button', { name: '建立研究' }).click();
  await assertPageText(page, ['行业语境 · 云计算基础设施', 'Alphabet', 'GOOGL Class A']);
  await page.close();
});

test('search shortcut survives unrelated keys and focuses the searchbox', async () => {
  const page = await browser.newPage();
  await page.goto(origin);
  const searchbox = page.getByRole('searchbox', { name: '搜索股票、公司或行业' });

  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('/');

  await assert.equal(await searchbox.evaluate((element) => element === document.activeElement), true);
  await page.close();
});

test('non-destination shell labels are inert while industry results are keyboard destinations', async () => {
  const page = await browser.newPage();
  await page.goto(origin);

  await assert.equal(await page.getByText('我的研究', { exact: true }).evaluate(isInteractive), false);
  await assert.equal(await page.getByRole('button', { name: '账户：研究员 XJ' }).count(), 0);

  await page.getByRole('searchbox', { name: '搜索股票、公司或行业' }).fill('GOOGL');
  await assert.equal(await page.getByTestId('industry-result').evaluate(isInteractive), true);
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
    '使用默认问题',
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
  await assert.equal(await page.getByLabel('你的假设').getAttribute('placeholder'), '例如：云业务的规模效应会抵消 AI 基础设施投入。');

  await page.getByRole('button', { name: '建立研究' }).click();
  await page.waitForURL((url) => url.searchParams.get('screen') === 'workbench' && url.searchParams.get('security') === 'GOOGL');
  await assertPageText(page, ['研究工作台', 'Alphabet', 'GOOGL Class A']);

  await page.close();
});

test('question templates and the default-question action update the submitted frame', async () => {
  const page = await browser.newPage();
  await page.goto(`${origin}/?screen=setup&security=GOOGL`);
  const question = page.getByLabel('你想回答什么问题？');

  await page.getByRole('button', { name: /资本回报/ }).click();
  assert.match(await question.inputValue(), /资本投入.*每股自由现金流/u);
  await question.fill('临时自定义问题');
  await page.getByRole('button', { name: '使用默认问题' }).click();
  await page.waitForURL((url) => url.searchParams.get('screen') === 'workbench');
  await assertPageText(page, [
    '以 3–5 年视角，这家公司靠什么创造价值，当前价格要求哪些假设成立？',
    '研究视角 · 3–5 年',
  ]);
  await page.close();
});

test('setup carries every edited research input into a shareable workbench URL and visible hypothesis context', async () => {
  const page = await browser.newPage();
  const question = '若储备现金转向 AI 基础设施，五年以上的每股现金回报会怎样？';
  const hypothesis = 'Cloud 的增量毛利将在五年内覆盖新增折旧。';
  const concern = '搜索单位变现下降会与资本强度上升同时发生。';
  await page.goto(`${origin}/?screen=setup&security=GOOGL`);

  await page.getByLabel('你想回答什么问题？').fill(question);
  await page.getByRole('radio', { name: '5 年以上', exact: true }).check();
  await page.locator('details summary').click();
  await page.getByLabel('你的假设', { exact: true }).fill(hypothesis);
  await page.getByLabel('你最担心什么？', { exact: true }).fill(concern);
  await page.getByRole('button', { name: '建立研究' }).click();

  const url = new URL(page.url());
  assert.deepEqual(Object.fromEntries(['q', 'h', 'hp', 'c'].map((key) => [key, url.searchParams.get(key)])), {
    q: question,
    h: '5-plus',
    hp: hypothesis,
    c: concern,
  });
  await assertPageText(page, ['研究问题', question, '研究视角 · 5 年以上', '你的假设', hypothesis, '你最担心什么？', concern]);
  await assert.equal(await page.locator('.user-hypothesis').evaluate((element) => element.closest('.context-ledger') === null), true);

  await page.getByRole('link', { name: 'B 模型优先' }).click();
  const variantUrl = new URL(page.url());
  assert.equal(variantUrl.searchParams.get('variant'), 'B');
  for (const [key, value] of [['q', question], ['h', '5-plus'], ['hp', hypothesis], ['c', concern]]) {
    assert.equal(variantUrl.searchParams.get(key), value);
  }
  await assertPageText(page, [question, '研究视角 · 5 年以上', hypothesis, concern]);

  await page.close();
});

test('workbench deep links use security as the canonical share class', async () => {
  const page = await browser.newPage();
  await page.goto(`${origin}/?screen=workbench&security=GOOG`);

  await assertPageText(page, ['Alphabet', 'GOOG Class C']);
  await assert.equal((await page.locator('body').innerText()).includes('GOOGL Class A'), false);
  await assert.equal(await page.getByRole('link', { name: '返回研究设置' }).getAttribute('href'), '?screen=setup&security=GOOG');
  await page.close();
});

test('default workbench leads with the current judgment and exactly four decision factors', async () => {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.goto(`${origin}/?screen=workbench&security=GOOGL`);

  await assertPageText(page, [
    '选择主体',
    '定义问题',
    '维护判断',
    'Alphabet',
    'GOOGL Class A',
    '以 3–5 年视角，这家公司靠什么创造价值，当前价格要求哪些假设成立？',
    '3–5 年',
    '模拟数据',
    '价格时间',
    '证据截止',
    '研究员暂定判断',
    '证据冲突，暂定判断',
    '当前允许得出的判断',
    '最大反证',
    '当前价格问题',
    '搜索广告经济性',
    'Cloud 单位经济性',
    'AI 资本效率',
    '竞争与监管',
    '公司如何赚钱',
    '行业位置',
    '预测与估值传导',
    '最大未知',
    '市场叙事 / Market Narrative',
    '市场语境，不是已证实驱动',
    '覆盖 31 位分析师',
    '预测区间',
    '近 30 天上修',
    '反推方法',
    '敏感性',
  ]);
  await assert.equal(await page.locator('[data-workbench-variant]').getAttribute('data-workbench-variant'), 'A');
  await assert.equal(await page.getByTestId('factor-row').count(), 4);
  await assert.equal(await page.locator('[data-key-factor]').count(), 4);
  await assert.equal(await page.locator('[data-primary-next-action]').count(), 1);
  await assert.equal(await page.locator('[data-primary-next-action] button[data-validation-toggle]').count(), 1);
  await assert.equal(await page.getByRole('heading', { name: '当前允许得出的判断', exact: true }).count(), 1);
  await assert.equal(await page.getByRole('heading', { name: '最大反证', exact: true }).count(), 1);
  await assert.equal(await page.getByRole('heading', { name: '当前价格问题', exact: true }).count(), 1);
  await assert.equal(await page.locator('aside').count(), 0);
  await assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);

  await page.close();
});

test('market context and typed expectations disclose distinct evidence boundaries', async () => {
  const page = await browser.newPage();
  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=A`);
  const narrative = page.locator('[data-market-narrative]');
  await assert.equal(await narrative.count(), 1);
  assert.equal((await narrative.innerText()).includes('驱动因素'), false);
  await page.getByRole('button', { name: /Cloud 单位经济性/ }).click();
  const detail = page.locator('#factor-detail-cloud');
  const detailText = await detail.innerText();
  for (const expected of [
    'Actual', 'Guidance', 'Consensus', 'Implied', 'House',
    '覆盖 31 位分析师', '预测区间 19.8%–23.4%', '近 30 天上修 0.6pct',
    '反推方法', '敏感性', '不是唯一答案',
  ]) assert.ok(detailText.includes(expected), `missing ${expected}`);
  const kinds = ['actual', 'guidance', 'consensus', 'implied', 'house'];
  for (const kind of kinds) await assert.equal(await detail.locator(`.metric-${kind}`).count(), 1);
  assert.equal(new Set(await detail.locator('[data-metric-kind]').evaluateAll((rows) => rows.map((row) => getComputedStyle(row).backgroundColor))).size, 5);
  await page.close();
});

test('primary next action creates and updates a session-local validation record', async () => {
  const page = await browser.newPage();
  const note = '先核对资本开支口径，再比较 Cloud 利润率修订。';
  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=A`);
  const action = page.getByRole('button', { name: '记录本次验证', exact: true });
  await assert.equal(await action.getAttribute('aria-expanded'), 'false');
  await action.focus();
  await page.keyboard.press('Enter');
  await assert.equal(await action.getAttribute('aria-expanded'), 'true');
  await assertPageText(page, ['验证记录草稿', '关联因素', 'AI 资本效率', '验证窗口', '2026 Q3 业绩披露']);
  await page.getByLabel('验证备注').fill(note);
  await page.getByRole('button', { name: '记录到本次会话' }).click();
  await assertPageText(page, ['已记录到本次浏览会话', '本次会话记录 · 1 条', note]);

  await page.reload();
  await page.getByRole('button', { name: '更新本次验证', exact: true }).click();
  await assertPageText(page, ['本次会话记录 · 1 条', note, '仅存于当前浏览标签页，不会同步到服务器']);
  const updatedNote = 'Cloud 指引口径已复核，下一步等待资本开支披露。';
  await page.getByLabel('验证备注').fill(updatedNote);
  await page.getByRole('button', { name: '更新本次会话记录' }).click();
  await assertPageText(page, ['已更新本次浏览会话记录', '本次会话记录 · 1 条', updatedNote]);
  assert.equal((await page.locator('[data-validation-record]').innerText()).includes('已持久化'), false);

  await page.close();
});

test('workbench variants never create page-level horizontal overflow at supported desktop widths', async () => {
  const page = await browser.newPage();

  for (const width of [1000, 1100, 1600]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const variant of ['A', 'B', 'C']) {
      await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=${variant}`);
      if (variant === 'A') await page.getByRole('button', { name: /搜索广告经济性/ }).click();
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        true,
        `${variant} overflows at ${width}px`,
      );
    }
  }

  await page.close();
});

test('essential factor, model, and memo evidence remains at least 11px on desktop', async () => {
  const page = await browser.newPage();
  const selectors = {
    A: [
      '.factor-table-head th', '.factor-name small', '.factor-state', '.factor-impact p', '.factor-impact small',
      '.metric-chip', '.metric-chip b', '.reasoning-step p', '.reasoning-step li', '.reasoning-arguments dt',
      '.reasoning-arguments dd', '.metric-table-head th', '.metric-row th', '.metric-row td',
    ],
    B: [
      '.driver-root small', '.driver-branch > span', '.driver-branch h3', '.driver-branch p', '.driver-branch small',
      '.model-ledger-head th', '.model-ledger-row th', '.model-ledger-row td', '.model-ledger-row small',
      '.model-reading span', '.model-reading p', '.model-judgment dt', '.model-judgment dd',
    ],
    C: [
      '.memo-heading > p:last-child', '.memo-number', '.memo-section h3', '.memo-section p', '.memo-section small',
      '.memo-disagreements li strong', '.memo-disagreements li p', '.memo-disagreements li small',
    ],
  };

  for (const width of [1000, 1100, 1180, 1600]) {
    await page.setViewportSize({ width, height: width === 1180 ? 820 : width === 1000 ? 800 : 1000 });
    for (const variant of ['A', 'B', 'C']) {
      await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=${variant}`);
      if (variant === 'A') await page.getByRole('button', { name: /搜索广告经济性/ }).click();
      const undersized = await page.locator(selectors[variant].join(',')).evaluateAll((elements) => elements
        .filter((element) => Number.parseFloat(getComputedStyle(element).fontSize) < 11)
        .map((element) => `${element.tagName}.${element.className}:${getComputedStyle(element).fontSize}`));
      assert.deepEqual(undersized, [], `${variant} has undersized evidence at ${width}px`);
    }
  }

  await page.close();
});

test('the dedicated variant switcher stays in normal flow and never overlaps research content', async () => {
  const page = await browser.newPage();
  const viewports = [
    { width: 1600, height: 1000 },
    { width: 1180, height: 820 },
    { width: 1000, height: 800 },
    { width: 390, height: 844 },
  ];

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const variant of ['A', 'B', 'C']) {
      await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=${variant}`);
      const geometry = await page.evaluate(() => {
        const main = document.querySelector('.workbench-screen').getBoundingClientRect();
        const dock = document.querySelector('[data-prototype-switcher]').getBoundingClientRect();
        const researchSelectors = [
          '.workbench-identity', '.workbench-question', '.context-ledger', '.judgment-band article',
          '.factor-section', '.business-transmission > div', '.driver-model > div', '.model-judgment',
          '.memo-heading', '.memo-section',
        ];
        const visibleResearch = [...document.querySelectorAll(researchSelectors.join(','))]
          .map((element) => element.getBoundingClientRect())
          .filter((rect) => rect.bottom > main.top && rect.top < main.bottom && rect.right > main.left && rect.left < main.right);
        const overlaps = visibleResearch.filter((rect) => (
          Math.max(rect.left, dock.left) < Math.min(rect.right, dock.right)
          && Math.max(rect.top, dock.top) < Math.min(rect.bottom, dock.bottom)
        )).length;
        return { dockPosition: getComputedStyle(document.querySelector('[data-prototype-switcher]')).position, dockBottom: dock.bottom, mainTop: main.top, dockTop: dock.top, overlaps };
      });
      assert.equal(geometry.dockPosition, 'static');
      assert.equal(geometry.dockBottom <= geometry.mainTop, true, `${variant} dock is not before main at ${viewport.width}x${viewport.height}`);
      assert.equal(geometry.dockBottom <= viewport.height, true, `${variant} dock leaves viewport at ${viewport.width}x${viewport.height}`);
      assert.equal(geometry.overlaps, 0, `${variant} dock overlaps visible research at ${viewport.width}x${viewport.height}`);
    }
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=A`);
  await page.close();
});

test('all routes fit the required responsive viewports and mobile preserves the reasoning order', async () => {
  const page = await browser.newPage();
  const viewports = [
    { width: 1600, height: 1000 },
    { width: 1180, height: 820 },
    { width: 1000, height: 800 },
    { width: 390, height: 844 },
  ];
  const routes = [
    '?screen=search',
    '?screen=setup&security=GOOGL',
    '?screen=workbench&security=GOOGL&variant=A',
    '?screen=workbench&security=GOOGL&variant=B',
    '?screen=workbench&security=GOOGL&variant=C',
  ];

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const route of routes) {
      await page.goto(`${origin}/${route}`);
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        true,
        `${route} overflows at ${viewport.width}x${viewport.height}`,
      );
    }
  }

  await assert.equal(await page.locator('link[href="./styles/responsive.css"]').count(), 1);
  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=A`);
  const orderedMobileSections = [
    '.workbench-identity',
    '.workbench-question',
    '.context-ledger',
    '.judgment-primary',
    '.judgment-band article:nth-child(2)',
    '.judgment-band article:nth-child(3)',
    '.market-narrative',
    '.factor-section',
    '.business-core',
    '.industry-position',
    '.valuation-transmission',
    '.largest-unknown',
    '.next-action',
  ];
  const sectionTops = await page.locator(orderedMobileSections.join(', ')).evaluateAll((elements, selectors) => {
    return selectors.map((selector) => document.querySelector(selector).getBoundingClientRect().top);
  }, orderedMobileSections);
  assert.equal(sectionTops.every((top, index) => index === 0 || top > sectionTops[index - 1]), true);
  assert.equal(await page.evaluate((selectors) => {
    const elements = selectors.map((selector) => document.querySelector(selector));
    return elements.every((element, index) => index === 0 || Boolean(
      elements[index - 1].compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING
    ));
  }, orderedMobileSections), true);

  await page.getByRole('button', { name: /搜索广告经济性/ }).click();
  for (const selector of ['[data-factor-table]', '#factor-detail-search-ads [data-metric-table]']) {
    const table = page.locator(selector);
    assert.equal(await table.evaluate((element) => element.scrollWidth <= element.clientWidth), true, `${selector} scrolls horizontally on mobile`);
    assert.equal(await table.locator('tbody tr').first().evaluate((row) => getComputedStyle(row).display), 'grid');
  }
  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=B`);
  const modelTable = page.locator('[data-model-table]');
  assert.equal(await modelTable.evaluate((element) => element.scrollWidth <= element.clientWidth), true);
  assert.equal(await modelTable.locator('tbody tr').first().evaluate((row) => getComputedStyle(row).display), 'grid');

  for (const route of routes) {
    await page.goto(`${origin}/${route}`);
    const undersizedTargets = await page.locator('a, button, summary, input, textarea').evaluateAll((controls) => controls
      .filter((control) => {
        const style = getComputedStyle(control);
        const bounds = control.getBoundingClientRect();
        return style.display !== 'none'
          && style.visibility !== 'hidden'
          && style.opacity !== '0'
          && control.getClientRects().length > 0
          && (bounds.width < 44 || bounds.height < 44);
      })
      .map((control) => `${control.tagName}:${control.getAttribute('aria-label') ?? control.textContent.trim().slice(0, 30)}`));
    assert.deepEqual(undersizedTargets, [], `${route} has undersized mobile targets`);
  }

  await page.close();
});

test('factor reasoning expands inline in a fixed order with typed metric boundaries', async () => {
  const page = await browser.newPage();
  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=A`);
  const factorButton = page.getByRole('button', { name: /搜索广告经济性/ });

  await assert.equal(await factorButton.getAttribute('aria-expanded'), 'false');
  await factorButton.focus();
  await page.keyboard.press('Enter');
  await assert.equal(await factorButton.getAttribute('aria-expanded'), 'true');

  const detail = page.locator('[data-testid="factor-detail"]:not([hidden])');
  await assert.equal(await page.getByTestId('factor-detail').count(), 4);
  await assert.equal(await detail.count(), 1);
  await assert.equal(
    await detail.evaluate((element) => element.closest('tr')?.previousElementSibling?.getAttribute('data-testid')),
    'factor-row',
  );
  const detailText = await detail.innerText();
  const orderedLabels = ['机制假设', '观察事实', '支持 / 反证 / 替代解释', '验证指标和日期', '财务与估值影响', '对当前判断的影响'];
  for (let index = 1; index < orderedLabels.length; index += 1) {
    assert.ok(detailText.indexOf(orderedLabels[index - 1]) < detailText.indexOf(orderedLabels[index]));
  }
  await assertPageText(page, ['Actual', 'Guidance', 'Consensus', 'Implied', 'House', '期间', '单位', '截至', '来源边界']);
  await assert.equal(await detail.locator('.metric-consensus').count(), 1);
  await assert.equal(await detail.locator('.metric-implied').count(), 1);
  await assert.notEqual(
    await detail.locator('.metric-consensus').getAttribute('class'),
    await detail.locator('.metric-implied').getAttribute('class'),
  );
  const evidenceCutoff = await page.locator('[data-evidence-cutoff]').getAttribute('data-evidence-cutoff');
  const metricDates = await detail.locator('[data-as-of]').evaluateAll((rows) => rows.map((row) => row.dataset.asOf));
  assert.ok(metricDates.every((date) => date <= evidenceCutoff));

  await factorButton.click();
  await assert.equal(await factorButton.getAttribute('aria-expanded'), 'false');
  await assert.equal(await detail.count(), 0);

  await page.close();
});

test('factor controls always resolve to persistent panels and only the selection is visible', async () => {
  const page = await browser.newPage();
  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=A`);
  const controls = page.locator('[aria-controls^="factor-detail-"]');

  await assert.equal(await controls.count(), 4);
  assert.equal(
    await controls.evaluateAll((buttons) => buttons.every((button) => document.getElementById(button.getAttribute('aria-controls')) !== null)),
    true,
  );
  await assert.equal(await page.locator('[data-testid="factor-detail"]:not([hidden])').count(), 0);

  await page.getByRole('button', { name: /搜索广告经济性/ }).click();
  await assert.equal(await page.locator('[data-testid="factor-detail"]:not([hidden])').count(), 1);
  await assert.equal(await page.locator('#factor-detail-search-ads').getAttribute('hidden'), null);

  await page.getByRole('button', { name: /Cloud 单位经济性/ }).click();
  await assert.equal(await page.locator('[data-testid="factor-detail"]:not([hidden])').count(), 1);
  await assert.equal(await page.locator('#factor-detail-search-ads').getAttribute('hidden'), '');
  await assert.equal(await page.locator('#factor-detail-cloud').getAttribute('hidden'), null);
  assert.deepEqual(await controls.evaluateAll((buttons) => buttons.map((button) => button.getAttribute('aria-expanded'))), [
    'false',
    'true',
    'false',
    'false',
  ]);

  await page.close();
});

test('factor, model, and metric comparisons use native tables with associated headers', async () => {
  const page = await browser.newPage();
  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=A`);

  const factorTable = page.locator('table[data-factor-table]');
  await assert.equal(await factorTable.count(), 1);
  for (const name of ['关键因素 / 为什么关键', '当前状态', 'House / Consensus / Implied', '财务影响 / 下一验证']) {
    await assert.equal(await factorTable.getByRole('columnheader', { name, exact: true }).count(), 1);
  }
  await assert.equal(await factorTable.locator('tbody > tr[data-key-factor]').count(), 4);
  await assert.equal(await factorTable.locator('tbody > tr[data-key-factor] > th[scope="row"]').count(), 4);
  await assert.equal(await factorTable.locator('tbody > tr[data-key-factor] > td').count(), 12);
  await assert.equal(await factorTable.locator('tbody > tr.factor-detail-row > td[colspan="4"]').count(), 4);

  await page.getByRole('button', { name: /搜索广告经济性/ }).click();
  const metricTable = page.locator('#factor-detail-search-ads table[data-metric-table]');
  await assert.equal(await metricTable.count(), 1);
  for (const name of ['类型', '指标', '数值', '期间', '单位', '截至', '来源边界']) {
    await assert.equal(await metricTable.getByRole('columnheader', { name, exact: true }).count(), 1);
  }
  await assert.equal(await metricTable.locator('tbody > tr').count(), 5);
  await assert.equal(await metricTable.locator('tbody > tr > th[scope="row"]').count(), 5);
  await assert.equal(await metricTable.locator('tbody > tr > td').count(), 30);

  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=B`);
  const modelTable = page.locator('table[data-model-table]');
  await assert.equal(await modelTable.count(), 1);
  for (const name of ['驱动', 'House', 'Consensus', 'Implied']) {
    await assert.equal(await modelTable.getByRole('columnheader', { name, exact: true }).count(), 1);
  }
  await assert.equal(await modelTable.locator('tbody > tr').count(), 4);
  await assert.equal(await modelTable.locator('tbody > tr > th[scope="row"]').count(), 4);
  await assert.equal(await modelTable.locator('tbody > tr > td').count(), 12);

  await page.close();
});

test('workbench offers three structurally distinct URL variants with canonical security identity', async () => {
  const page = await browser.newPage();

  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=A`);
  await assert.equal(await page.locator('[data-workbench-variant="A"] .variant-a').count(), 1);

  await page.goto(`${origin}/?screen=workbench&security=GOOG&variant=B`);
  const variantB = page.locator('[data-workbench-variant="B"]');
  await assert.equal(await variantB.locator('.variant-b').count(), 1);
  await assert.equal(await variantB.locator('.variant-a, .factor-table').count(), 0);
  await assertPageText(page, [
    '经营驱动树', '模型分歧', 'House', 'Consensus', 'Implied', '判断摘要', 'GOOG Class C',
    '最大反证', '当前价格问题', '公司如何赚钱', '行业位置', '预测与估值传导', '最大未知', '唯一下一动作',
  ]);
  const variantBText = await variantB.innerText();
  assert.ok(variantBText.indexOf('经营驱动树') < variantBText.indexOf('判断摘要'));
  assert.ok(variantBText.indexOf('模型分歧') < variantBText.indexOf('判断摘要'));
  await assert.equal(await variantB.locator('[data-factor-name]').count(), 4);

  await page.goto(`${origin}/?screen=workbench&security=GOOG&variant=C`);
  const variantC = page.locator('[data-workbench-variant="C"]');
  await assert.equal(await variantC.locator('.pm-memo').count(), 1);
  await assert.equal(await variantC.locator('.variant-a, .variant-b, .factor-table').count(), 0);
  const memoText = await variantC.innerText();
  const memoOrder = ['研究问题', '关键分歧', '估值传导', '最强反证 / 证伪', '唯一下一动作'];
  for (let index = 1; index < memoOrder.length; index += 1) {
    assert.ok(memoText.indexOf(memoOrder[index - 1]) < memoText.indexOf(memoOrder[index]));
  }
  await assert.equal(await variantC.locator('[data-factor-name]').count(), 4);
  await assertPageText(page, ['GOOG Class C']);

  await page.close();
});

test('every displayed consensus includes numeric coverage, range, and revision direction', async () => {
  const page = await browser.newPage();
  const assertDisclosure = async (locator, label) => {
    const texts = await locator.evaluateAll((elements) => elements.map((element) => element.textContent));
    assert.ok(texts.length > 0, `${label} has no consensus disclosures`);
    for (const text of texts) {
      assert.match(text, /覆盖\s*\d+\s*位/u, `${label} lacks coverage: ${text}`);
      assert.match(text, /预测区间\s*-?\d/u, `${label} lacks a numeric range: ${text}`);
      assert.match(text, /近\s*30\s*天.*(?:上修|下修|持平)/u, `${label} lacks revision direction: ${text}`);
      assert.equal(text.includes('见各因素'), false);
    }
  };

  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=A`);
  await assertDisclosure(page.locator('.metric-chip.metric-consensus'), 'factor table');
  await assertDisclosure(page.locator('.metric-row.metric-consensus'), 'metric table');
  await assertDisclosure(page.locator('[data-market-narrative] dd').first(), 'market narrative');

  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=B`);
  await assertDisclosure(page.locator('.model-consensus'), 'model table');
  await page.close();
});

test('Variant B labels its compact sensitivity and pairs it with complete typed ledgers', async () => {
  const page = await browser.newPage();
  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=B`);
  await assertPageText(page, ['窄范围模型敏感性', '不是完整五类数据台账', '查看完整五类数据台账']);

  const firstHouse = page.locator('.model-house').first();
  const firstImplied = page.locator('.model-implied').first();
  for (const expected of ['内部暂定假设', '截至 2026-08-19']) assert.ok((await firstHouse.innerText()).includes(expected));
  for (const expected of ['反推方法', '截至 2026-08-19']) assert.ok((await firstImplied.innerText()).includes(expected));

  await page.getByText('查看完整五类数据台账', { exact: true }).click();
  const ledgers = page.locator('[data-complete-typed-ledgers] [data-metric-table]');
  await assert.equal(await ledgers.count(), 4);
  for (let index = 0; index < 4; index += 1) {
    assert.deepEqual(await ledgers.nth(index).locator('[data-metric-kind]').evaluateAll((rows) => rows.map((row) => row.dataset.metricKind)), [
      'Actual', 'Guidance', 'Consensus', 'Implied', 'House',
    ]);
  }
  await page.close();
});

test('top-level consensus summaries define metric, forecast period, range, coverage, and cutoff', async () => {
  const page = await browser.newPage();
  const cases = [
    ['GOOGL', ['FY2027E adjusted EPS', '2.18–2.54 USD/股', '12 estimates', '截至 2026-08-18']],
    ['300750.SZ', ['FY2027E adjusted EPS', '12.4–15.8 CNY/股', '18 estimates', '截至 2026-08-18']],
  ];
  for (const [security, expected] of cases) {
    await page.goto(`${origin}/?screen=workbench&security=${security}&variant=A`);
    const summary = await page.locator('[data-market-narrative] dd').first().innerText();
    for (const text of expected) assert.ok(summary.includes(text), `${security} missing ${text}`);
  }
  await page.close();
});

test('docked prototype switcher wraps, survives unrelated keys, and preserves all URL research state', async () => {
  const page = await browser.newPage();
  const state = '&q=%E8%87%AA%E5%AE%9A%E4%B9%89%E9%97%AE%E9%A2%98&h=1-2&hp=%E5%88%9D%E5%A7%8B%E5%81%87%E8%AE%BE&c=%E6%9C%80%E5%A4%A7%E6%8B%85%E5%BF%A7';
  await page.goto(`${origin}/?screen=workbench&security=GOOG&variant=A${state}`);
  const switcher = page.locator('[data-prototype-switcher]');

  await assert.equal(await switcher.count(), 1);
  await assert.equal(await switcher.evaluate((element) => element.closest('main') === null), true);
  await assertPageText(page, ['PROTOTYPE', 'A 判断优先', 'B 模型优先', 'C PM 备忘录']);
  assert.deepEqual(await switcher.locator('a').evaluateAll((links) => links.map((link) => {
    const url = new URL(link.href);
    return Object.fromEntries(['screen', 'security', 'variant', 'q', 'h', 'hp', 'c'].map((key) => [key, url.searchParams.get(key)]));
  })), ['A', 'B', 'C'].map((variant) => ({
    screen: 'workbench', security: 'GOOG', variant, q: '自定义问题', h: '1-2', hp: '初始假设', c: '最大担忧',
  })));
  assert.equal(
    await switcher.locator('button, a').evaluateAll((controls) => controls.every((control) => {
      const bounds = control.getBoundingClientRect();
      return bounds.width >= 44 && bounds.height >= 44;
    })),
    true,
  );
  await switcher.locator('a[aria-current="page"]').focus();
  await page.keyboard.press('KeyX');
  await page.keyboard.press('ArrowRight');
  await page.waitForURL((url) => url.searchParams.get('variant') === 'B');
  await assert.equal(await page.locator('[data-workbench-variant="B"]').count(), 1);
  await assertPageText(page, ['GOOG Class C', '自定义问题', '初始假设', '最大担忧']);

  await page.evaluate(() => {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', altKey: true, bubbles: true }));
  });
  await assert.equal(new URL(page.url()).searchParams.get('variant'), 'B');

  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowRight');
  await page.waitForURL((url) => url.searchParams.get('variant') === 'C', { timeout: 1000 });
  await page.getByRole('button', { name: '下一个原型方案' }).click();
  await page.waitForURL((url) => url.searchParams.get('variant') === 'A');
  await page.getByRole('button', { name: '上一个原型方案' }).click();
  await page.waitForURL((url) => url.searchParams.get('variant') === 'C');

  await page.reload();
  await assert.equal(await page.locator('[data-workbench-variant="C"]').count(), 1);
  await assertPageText(page, ['GOOG Class C']);

  await page.evaluate(() => {
    const textarea = document.createElement('textarea');
    textarea.setAttribute('aria-label', '临时输入');
    document.body.append(textarea);
    textarea.focus();
  });
  await page.keyboard.press('ArrowLeft');
  await assert.equal(new URL(page.url()).searchParams.get('variant'), 'C');

  await page.evaluate(() => {
    const input = document.createElement('input');
    document.body.append(input);
    input.focus();
  });
  await page.keyboard.press('ArrowLeft');
  await assert.equal(new URL(page.url()).searchParams.get('variant'), 'C');

  await page.evaluate(() => {
    const editable = document.createElement('div');
    editable.contentEditable = 'true';
    document.body.append(editable);
    editable.focus();
  });
  await page.keyboard.press('ArrowLeft');
  await assert.equal(new URL(page.url()).searchParams.get('variant'), 'C');

  await page.close();
});

test('variant arrow shortcuts are scoped exclusively to prototype switcher focus', async () => {
  const page = await browser.newPage();
  await page.goto(`${origin}/?screen=workbench&security=GOOGL&variant=A`);

  await page.evaluate(() => {
    document.body.tabIndex = -1;
    document.body.focus();
  });
  await assert.equal(await page.locator('body').evaluate((element) => element === document.activeElement), true);
  await page.keyboard.press('ArrowRight');
  await assert.match(page.url(), /variant=A$/);

  await page.getByRole('button', { name: /搜索广告经济性/ }).focus();
  await page.keyboard.press('ArrowRight');
  await assert.match(page.url(), /variant=A$/);
  await assert.equal(await page.getByRole('button', { name: /搜索广告经济性/ }).getAttribute('aria-expanded'), 'false');

  await page.getByRole('link', { name: '返回研究设置' }).focus();
  await page.keyboard.press('ArrowRight');
  await assert.match(page.url(), /variant=A$/);

  await page.locator('[data-prototype-switcher] a[aria-current="page"]').focus();
  await page.keyboard.press('ArrowRight');
  await page.waitForURL(/variant=B$/);
  await assert.equal(
    await page.locator('[data-prototype-switcher] a[aria-current="page"]').evaluate((element) => element === document.activeElement),
    true,
  );

  await page.close();
});

test('in-app navigation and browser history move focus to the new route heading', async () => {
  const page = await browser.newPage();
  await page.goto(`${origin}/?screen=setup&security=GOOGL`);
  await page.getByRole('button', { name: '建立研究' }).click();

  await assert.equal(await page.locator('h1').evaluate((element) => element === document.activeElement), true);
  await page.goBack();
  await assert.equal(await page.locator('h1').evaluate((element) => element === document.activeElement), true);
  await assert.equal(await page.locator('h1').innerText(), 'Alphabet');
  await page.close();
});

test('new source and rendered routes keep the independent language boundary', async () => {
  const forbiddenTerms = [
    ['ResearchC', 'ase'].join(''),
    ['Research', 'Run'].join(''),
    ['Event', ' Research'].join(''),
    ['证据', '图谱'].join(''),
    ['自动研究', '完成'].join(''),
    ['r', 'un'].join(''),
    ['worker', ' monitor'].join(''),
    ['news', ' feed'].join(''),
    ['source-count', ' KPI'].join(''),
    ['买', '入'].join(''),
    ['卖', '出'].join(''),
    ['目标', '价'].join(''),
    ['AI ', '置信度'].join(''),
    ['综合', '评分'].join(''),
    ['新闻', '导致'].join(''),
  ];
  const standaloneForbidden = new RegExp(`\\b${['C', 'ase'].join('')}\\b`);
  const sourceFiles = [
    'index.html',
    'app.js',
    'screens/search.js',
    'screens/setup.js',
    'screens/workbench.js',
    'styles/tokens.css',
    'styles/app.css',
    'styles/responsive.css',
    'capture.mjs',
    'README.md',
    'contract.test.mjs',
  ];

  for (const relativePath of sourceFiles) {
    const source = await readFile(join(root, relativePath), 'utf8');
    for (const term of forbiddenTerms) assert.equal(source.includes(term), false, `${relativePath} contains forbidden copy`);
    assert.equal(standaloneForbidden.test(source), false, `${relativePath} contains forbidden standalone copy`);
  }

  const page = await browser.newPage();
  for (const route of ['search', 'setup', 'workbench']) {
    await page.goto(`${origin}/?screen=${route}&security=GOOGL`);
    const text = await page.locator('body').innerText();
    for (const term of forbiddenTerms) assert.equal(text.includes(term), false, `${route} renders forbidden copy`);
    assert.equal(standaloneForbidden.test(text), false, `${route} renders forbidden standalone copy`);
  }
  await page.close();
});

test('every rendered route and workbench variant excludes decision-language shortcuts', async () => {
  const forbiddenTerms = [
    ['ResearchC', 'ase'].join(''),
    ['自动研究', '完成'].join(''),
    ['买', '入'].join(''),
    ['卖', '出'].join(''),
    ['目标', '价'].join(''),
    ['AI ', '置信度'].join(''),
    ['综合', '评分'].join(''),
    ['新闻', '导致'].join(''),
  ];
  const routes = [
    '?screen=search',
    '?screen=setup&security=GOOGL',
    '?screen=workbench&security=GOOGL&variant=A',
    '?screen=workbench&security=GOOGL&variant=B',
    '?screen=workbench&security=GOOGL&variant=C',
  ];
  const page = await browser.newPage();

  for (const route of routes) {
    await page.goto(`${origin}/${route}`);
    const renderedText = await page.locator('body').innerText();
    for (const term of forbiddenTerms) assert.equal(renderedText.includes(term), false, `${route} renders forbidden copy`);
  }

  await page.close();
});

test('failed capture generation leaves the complete published image set unchanged', async () => {
  const captureFiles = [
    '01-search.png',
    '02-setup.png',
    '03-workbench-a.png',
    '04-workbench-b.png',
    '05-workbench-c.png',
    '06-workbench-mobile.png',
  ];
  const outputDirectory = join(root, 'output');
  const before = await Promise.all(captureFiles.map((file) => readFile(join(outputDirectory, file))));
  const child = spawnSync(process.execPath, [join(root, 'capture.mjs')], {
    encoding: 'utf8',
    env: { ...process.env, CAPTURE_FAIL_AFTER: '3' },
  });
  const after = await Promise.all(captureFiles.map((file) => readFile(join(outputDirectory, file))));
  const leftovers = (await readdir(outputDirectory)).filter((file) => file.includes('.capture-'));

  assert.notEqual(child.status, 0, 'injected capture failure must make the command fail');
  assert.deepEqual(after, before, 'capture failure must not publish a mixed generation');
  assert.deepEqual(leftovers, [], 'capture failure must clean staged siblings');
});

async function assertPageText(page, fragments) {
  const text = await page.locator('body').innerText();
  for (const fragment of fragments) assert.match(text, new RegExp(escapeRegex(fragment)));
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function isInteractive(element) {
  return element.matches('a, button, input, select, textarea, [tabindex]');
}
