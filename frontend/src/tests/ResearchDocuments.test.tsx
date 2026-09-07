import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { ResearchDocuments } from '@/workbench/ResearchDocuments';
const doc = { id: 'd', title: '公告原件', parse_state: 'parsed', span_count: 1, available_at: '2026-09-06T00:00:00Z', source_url: 'https://example.test/source' };
const response = (x: unknown) => new Response(JSON.stringify(x));
afterEach(() => vi.unstubAllGlobals());
it('loads pages lazily and reads source spans through the scoped document endpoint', async () => {
  const fetcher = vi.fn((url: string) => Promise.resolve(response(url.endsWith('/documents/d') ? { document: doc, spans: [{ id: 's', document_version_id: 'd', verbatim_text: '冻结原文内容', locator: { page: 2 } }] } : { items: [url.includes('cursor=') ? { ...doc, id: 'e', title: '第二份公告' } : doc], page: { has_more: !url.includes('cursor='), next_cursor: url.includes('cursor=') ? null : 'next' } })));
  vi.stubGlobal('fetch', fetcher);
  render(<ResearchDocuments caseId="a" />);
  expect(fetcher).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: '查看原始资料' }));
  await screen.findByText('公告原件');
  await userEvent.click(screen.getByRole('button', { name: '加载更多资料' }));
  await screen.findByText('第二份公告');
  await userEvent.click(screen.getAllByRole('button', { name: '查看冻结原文' })[0]!);
  await screen.findByText('冻结原文内容');
  expect(fetcher.mock.calls.at(-1)?.[0]).toBe('/api/v1/event-research/a/documents/d');
});
it('rejects spans belonging to another document', async () => {
  vi.stubGlobal('fetch', vi.fn((url: string) => Promise.resolve(response(url.endsWith('/documents/d') ? { document: doc, spans: [{ id: 's', document_version_id: 'other', verbatim_text: '错误原文', locator: {} }] } : { items: [doc], page: { has_more: false, next_cursor: null } }))));
  render(<ResearchDocuments caseId="a" />);
  await userEvent.click(screen.getByRole('button', { name: '查看原始资料' }));
  await userEvent.click(await screen.findByRole('button', { name: '查看冻结原文' }));
  await screen.findByRole('alert');
  expect(screen.queryByText('错误原文')).not.toBeInTheDocument();
});
