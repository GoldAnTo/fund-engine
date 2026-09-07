import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { RunAIUsage, CaseAIUsage } from '@/workbench/RunAIUsage';
const base = { run_id: 'r', coverage: 'recorded_attributed_operations_only', operation_count: 0, reported_attempt_count: 0, unavailable_attempt_count: 0, unavailable_operation_count: 0, reported_prompt_tokens: 0, reported_completion_tokens: 0, reported_total_tokens: 0, recorded_total_tokens: null };
afterEach(() => vi.unstubAllGlobals());
it('loads lazily and does not equate absent records with zero usage', async () => {
  const fetcher = vi.fn(() => Promise.resolve(new Response(JSON.stringify(base)))); vi.stubGlobal('fetch', fetcher);
  render(<RunAIUsage runId="r" />); expect(fetcher).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: '查看模型用量' }));
  await screen.findByText('暂无可归属的调用记录，不能据此认定用量为零。');
});
it('shows partial token totals with the unknown portion explicitly identified', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ ...base, operation_count: 2, reported_attempt_count: 1, unavailable_attempt_count: 1, unavailable_operation_count: 1, reported_prompt_tokens: 3, reported_completion_tokens: 2, reported_total_tokens: 5 })))));
  render(<RunAIUsage runId="r" />); await userEvent.click(screen.getByRole('button', { name: '查看模型用量' }));
  await screen.findByText('5（输入 3，输出 2）');
  expect(screen.getByText(/记录用量不完整：1 次/)).toBeInTheDocument();
});
it('rejects usage returned for a different run', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ ...base, run_id: 'other' })))));
  render(<RunAIUsage runId="r" />); await userEvent.click(screen.getByRole('button', { name: '查看模型用量' }));
  await screen.findByRole('alert'); expect(screen.queryByText(/暂无可归属/)).not.toBeInTheDocument();
});

it('loads Case totals from the Case-scoped endpoint', async () => {
  const fetcher = vi.fn((_url: string) => Promise.resolve(new Response(JSON.stringify({ ...base, run_id: undefined, case_id: 'case', operation_count: 1, recorded_total_tokens: 0 }))));
  vi.stubGlobal('fetch', fetcher);
  render(<CaseAIUsage caseId="case" />);
  await userEvent.click(screen.getByRole('button', { name: '查看研究累计模型用量' }));
  await screen.findByText('已保存记录的 token 总量：0。仍不包含未归属或未保存的调用。');
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/v1/research-cases/case/ai-usage');
});
