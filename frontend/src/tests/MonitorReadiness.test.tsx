import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { MonitorReadiness } from '@/workbench/MonitorReadiness';
const detail = { monitor: { version: 3, budget: 12, allowed_source_types: ['company_disclosure'], factor_ids: ['f'] }, confirmed_factors: [{ id: 'f', statement: '订单趋势' }] };
const response = (x: unknown) => new Response(JSON.stringify(x));
afterEach(() => vi.unstubAllGlobals());
it('loads selected factors only and shows blocked protocol with next action', async () => {
  const fetcher = vi.fn((url: string) => Promise.resolve(response(url.endsWith('/monitor') ? detail : { status: 'blocked', reason_codes: ['missing_binding'], next_action: '请确认结果指标' })));
  vi.stubGlobal('fetch', fetcher);
  render(<MonitorReadiness caseId="a" />);
  expect(fetcher).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: '核对监控范围与协议' }));
  await screen.findByText('请确认结果指标');
  expect(screen.getByText('订单趋势')).toBeInTheDocument();
  expect(screen.getByText('协议尚未通过')).toBeInTheDocument();
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['/api/v1/research-cases/a/monitor', '/api/v1/theses/f/researchability']);
});
it('does not call a factor endpoint when a selected factor is no longer confirmed', async () => {
  const fetcher = vi.fn(() => Promise.resolve(response({ ...detail, confirmed_factors: [] })));
  vi.stubGlobal('fetch', fetcher);
  render(<MonitorReadiness caseId="a" />);
  await userEvent.click(screen.getByRole('button', { name: '核对监控范围与协议' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('已确认因素不完整');
  expect(fetcher).toHaveBeenCalledOnce();
});
