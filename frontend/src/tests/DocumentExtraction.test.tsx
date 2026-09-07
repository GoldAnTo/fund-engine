import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { DocumentExtraction } from '@/workbench/DocumentExtraction';
afterEach(() => vi.unstubAllGlobals());
it('requires confirmation and keeps extracted claims awaiting review', async () => {
  const fetcher = vi.fn((_url: string) => Promise.resolve(new Response(JSON.stringify({ document_version_id: 'd', candidate_count: 1, reason: null, candidates: [{ id: 'c', claim_type: 'fact', normalized_text: '订单增长', quote: '原文订单增长', quote_start: 0, quote_end: 6, review_state: 'awaiting_review' }] }))));
  vi.stubGlobal('fetch', fetcher);
  render(<DocumentExtraction caseId="a" documentId="d" allowed extractionState="not_attempted" />);
  const button = screen.getByRole('button', { name: '从冻结资料提取候选' });
  expect(button).toBeDisabled();
  await userEvent.click(screen.getByLabelText('我已核对来源，确认调用模型提取待审核候选'));
  await userEvent.click(button);
  await screen.findByText('已生成 1 条待审核候选，尚未发布正式陈述。');
  expect(screen.getByText('原文订单增长')).toBeInTheDocument();
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/v1/documents/d/extract?case_id=a');
  expect(button).toBeDisabled();
});
it('blocks sources without AI permission', () => {
  render(<DocumentExtraction caseId="a" documentId="d" allowed={false} extractionState="not_attempted" />);
  expect(screen.getByRole('button', { name: '从冻结资料提取候选' })).toBeDisabled();
});
it('locks an uncertain attempt until the document state is refreshed', async () => {
  const fetcher = vi.fn(() => Promise.reject(new TypeError('network unavailable')));
  vi.stubGlobal('fetch', fetcher);
  render(<DocumentExtraction caseId="a" documentId="d" allowed extractionState="not_attempted" />);
  await userEvent.click(screen.getByLabelText('我已核对来源，确认调用模型提取待审核候选'));
  await userEvent.click(screen.getByRole('button', { name: '从冻结资料提取候选' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('请刷新原始资料核对抽取状态');
  expect(screen.getByRole('button', { name: '从冻结资料提取候选' })).toBeDisabled();
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it('does not display candidates returned for a different document', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ document_version_id: 'other', candidate_count: 0, candidates: [], reason: null })))));
  render(<DocumentExtraction caseId="a" documentId="d" allowed extractionState="failed" />);
  await userEvent.click(screen.getByLabelText('我已核对来源，确认调用模型提取待审核候选'));
  await userEvent.click(screen.getByRole('button', { name: '从冻结资料提取候选' }));
  await screen.findByRole('alert');
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});
