import { afterEach, expect, it, vi } from 'vitest';
import { researchRunApi } from '@/data/researchRunApi';
const signal = new AbortController().signal;
const reply = (data: unknown) => vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(data))));
afterEach(() => vi.unstubAllGlobals());
it('rejects detail from a different case', async () => {
  reply({ id: 'r', case_id: 'other', status: 'running' });
  await expect(researchRunApi.detail('a', 'r', signal)).rejects.toThrow('数据格式');
});
it('requests event cursor and rejects mismatched or unordered events', async () => {
  reply({ run_id: 'r', items: [{ seq: 4, created_at: 'now', details: {} }], has_more: false });
  await researchRunApi.events('r', signal, 3);
  expect(fetch).toHaveBeenCalledWith('/api/v1/research-runs/r/events?limit=50&after_seq=3', expect.objectContaining({ signal }));
  reply({ run_id: 'other', items: [], has_more: false });
  await expect(researchRunApi.events('r', signal)).rejects.toThrow('数据格式');
  reply({ run_id: 'r', items: [{ seq: 3, created_at: 'now', details: {} }], has_more: true });
  await expect(researchRunApi.events('r', signal, 3)).rejects.toThrow('数据格式');
});
it('posts actor and change_reason and refuses an unconfirmed cancellation', async () => {
  reply({ id: 'r', status: 'running' });
  await expect(researchRunApi.cancel('r', { actor: '研究员', change_reason: '范围变化' }, signal)).rejects.toThrow('数据格式');
  expect(fetch).toHaveBeenCalledWith('/api/v1/research-runs/r/cancel', expect.objectContaining({ method: 'POST', body: JSON.stringify({ actor: '研究员', change_reason: '范围变化' }) }));
});
it('rejects malformed event pages and ambiguous continuation', async () => {
  for (const page of [{ run_id: 'r', items: [], has_more: true }, { run_id: 'r', items: [{ seq: 1, created_at: 'now', details: [] }], has_more: false }]) {
    reply(page);
    await expect(researchRunApi.events('r', signal)).rejects.toThrow('数据格式');
  }
});
it('encodes the recorded run-list cursor and rejects a repeated cursor', async () => {
  const cursor = '2026-09-06T01:00:00+00:00|r';
  reply({ items: [], has_more: false, next_cursor: null });
  await researchRunApi.list('case/a', signal, cursor);
  expect(fetch).toHaveBeenCalledWith('/api/v1/research-cases/case%2Fa/runs?limit=20&after_created_at=2026-09-06T01%3A00%3A00%2B00%3A00&after_id=r', expect.anything());
  reply({ items: [], has_more: true, next_cursor: cursor });
  await expect(researchRunApi.list('a', signal, cursor)).rejects.toThrow('数据格式');
});
