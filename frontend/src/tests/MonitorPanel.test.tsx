import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { MonitorPanel } from '@/workbench/MonitorPanel';
const monitor = { id: 'm', version: 1, status: 'active', frequency: 'daily_20_00', budget: 20, next_verification_event: '下次公告', changed_by: '研究员', change_reason: '初始计划' };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
afterEach(() => vi.unstubAllGlobals());
it('loads lazily and pauses then resumes using authoritative reads and audit reasons', async () => {
  let current = monitor;
  const fetcher = vi.fn((url: string, init: RequestInit) => {
    if (init.method === 'POST') current = { ...current, status: url.endsWith('/paused') ? 'paused' : 'active', version: current.version + 1 };
    return Promise.resolve(response(init.method === 'POST' ? current : { monitor: current, next_scheduled_at: current.status === 'active' ? '2026-09-07T12:00:00Z' : null }));
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MonitorPanel caseId="case/a" actor="研究员" />);
  expect(fetcher).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: '查看监控计划' }));
  await screen.findByText('下次公告');
  expect(screen.getByRole('button', { name: '暂停未来调度' })).toBeDisabled();
  await userEvent.type(screen.getByLabelText('监控变更原因'), '等待新公告');
  await userEvent.click(screen.getByRole('button', { name: '暂停未来调度' }));
  await screen.findByText('已暂停');
  expect(fetcher.mock.calls.find(([url]) => url.endsWith('/paused'))?.[1].body).toBe(JSON.stringify({ actor: '研究员', change_reason: '等待新公告', expected_version: 1 }));
  await userEvent.type(screen.getByLabelText('监控变更原因'), '继续跟踪');
  await userEvent.click(screen.getByRole('button', { name: '恢复未来调度' }));
  await screen.findByText('调度中');
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/v1/research-cases/case%2Fa/monitor');
});
it('blocks another mutation when post-write refresh fails and recovers on explicit refresh', async () => {
  let written = false;
  let recovered = false;
  vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => {
    if (init.method === 'POST') { written = true; return Promise.resolve(response({ ...monitor, status: 'paused' })); }
    return Promise.resolve(written && !recovered ? response({}, 503) : response({ monitor: { ...monitor, status: written ? 'paused' : 'active' }, next_scheduled_at: null }));
  }));
  render(<MonitorPanel caseId="a" actor="研究员" />);
  await userEvent.click(screen.getByRole('button', { name: '查看监控计划' }));
  await screen.findByText('下次公告');
  await userEvent.type(screen.getByLabelText('监控变更原因'), '暂停');
  await userEvent.click(screen.getByRole('button', { name: '暂停未来调度' }));
  await screen.findByRole('alert');
  expect(screen.getByRole('button', { name: '暂停未来调度' })).toBeDisabled();
  expect(screen.getByLabelText('监控变更原因')).toHaveValue('暂停');
  recovered = true;
  await userEvent.click(screen.getByRole('button', { name: '刷新监控计划' }));
  await screen.findByText('已暂停');
});
it('shows missing monitor without offering a status mutation', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(response({ monitor: null, next_scheduled_at: null }))));
  render(<MonitorPanel caseId="a" actor="研究员" />);
  await userEvent.click(screen.getByRole('button', { name: '查看监控计划' }));
  await screen.findByText('尚未配置监控计划。');
  expect(screen.queryByLabelText('监控变更原因')).not.toBeInTheDocument();
});
it.each([{ ...monitor, status: 'unknown' }, { ...monitor, budget: -1 }, { ...monitor, version: 1.5 }])('rejects malformed monitor state before enabling writes', async (value) => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(response({ monitor: value, next_scheduled_at: null }))));
  render(<MonitorPanel caseId="a" actor="研究员" />);
  await userEvent.click(screen.getByRole('button', { name: '查看监控计划' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('数据格式');
  expect(screen.queryByLabelText('监控变更原因')).not.toBeInTheDocument();
});
it('ignores a late response when switching cases', async () => {
  let resolve!: (value: Response) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
  const view = render(<MonitorPanel caseId="a" actor="研究员" />);
  await userEvent.click(screen.getByRole('button', { name: '查看监控计划' }));
  view.rerender(<MonitorPanel caseId="b" actor="研究员" />);
  resolve(response({ monitor, next_scheduled_at: null }));
  expect(screen.queryByLabelText('监控变更原因')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '查看监控计划' })).toBeEnabled();
});
it.each(['active', 'paused'])('explains unsupported legacy frequency and only permits pausing (%s)', async (status) => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(response({ monitor: { ...monitor, status, frequency: 'weekly_monday' }, next_scheduled_at: null }))));
  render(<MonitorPanel caseId="a" actor="研究员" />);
  await userEvent.click(screen.getByRole('button', { name: '查看监控计划' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('频率不受支持');
  expect(screen.queryByText('调度中', { exact: true })).not.toBeInTheDocument();
  await userEvent.type(screen.getByLabelText('监控变更原因'), '处理历史计划');
  if (status === 'active') expect(screen.getByRole('button', { name: '暂停未来调度' })).toBeEnabled();
  else expect(screen.getByRole('button', { name: '恢复未来调度' })).toBeDisabled();
});
