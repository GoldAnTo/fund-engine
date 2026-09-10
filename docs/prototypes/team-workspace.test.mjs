import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import test from 'node:test';

const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const html = readFileSync(new URL('./fundclaw-team-workspace-v2.html', import.meta.url), 'utf8');

function workspace(t) {
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost/' });
  t.after(() => dom.window.close());
  return { window: dom.window, query: (selector) => dom.window.document.querySelector(selector) };
}

test('draft and recipient belong to their research and restore on return', (t) => {
  const { query } = workspace(t);
  const first = query('[data-thread]').dataset.thread;
  const second = query('[data-thread]:last-child').dataset.thread;
  query('#message').value = '仅用于第一项研究的草稿';
  query('#recipient').value = 'finance';
  query(`[data-thread="${second}"]`).click();
  assert.equal(query('#message').value, '');
  assert.equal(query('#recipient').value, 'team');
  query('#message').value = '第二项研究草稿';
  query(`[data-thread="${first}"]`).click();
  assert.equal(query('#message').value, '仅用于第一项研究的草稿');
  assert.equal(query('#recipient').value, 'finance');
});

test('creating a research leaves earlier draft intact without importing sample evidence', (t) => {
  const { query } = workspace(t);
  const first = query('[data-thread]').dataset.thread;
  query('#message').value = '保留已有研究输入';
  query('[data-new]').click();
  assert.equal(query('#message').value, '');
  assert.equal(query('#recipient').value, 'team');
  assert.match(query('#right-content').textContent, /暂无本轮证据/);
  query(`[data-thread="${first}"]`).click();
  assert.equal(query('#message').value, '保留已有研究输入');
});

test('empty input does not create research; user markup is rendered as text', (t) => {
  const { window, query } = workspace(t);
  query('[data-new]').click();
  query('#message').value = '  ';
  query('#composer').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
  assert.equal(query('#thread-count').textContent, '2');
  query('#message').value = '<img src=x onerror="alert(1)"> 审计输入';
  query('#composer').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
  assert.equal(query('#thread-count').textContent, '3');
  assert.equal(query('#feed img'), null);
  assert.match(query('#feed').textContent, /<img src=x/);
  assert.match(query('#right-content').textContent, /暂无本轮证据/);
});

test('an unsubmitted new research draft also survives switching to an existing research', (t) => {
  const { query } = workspace(t);
  const first = query('[data-thread]').dataset.thread;
  query('[data-new]').click();
  query('#message').value = '还没提交的新研究草稿';
  query('#recipient').value = 'finance';
  query(`[data-thread="${first}"]`).click();
  query('[data-new]').click();
  assert.equal(query('#message').value, '还没提交的新研究草稿');
  assert.equal(query('#recipient').value, 'finance');
});
