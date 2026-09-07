import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { AppErrorBoundary } from '../app/AppErrorBoundary';

afterEach(() => vi.restoreAllMocks());

it('shows a safe focused recovery screen without rendering internal exception details', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  function Broken(): never { throw new Error('private-provider-token'); }
  const retry = vi.fn();
  render(<AppErrorBoundary onRetry={retry}><Broken /></AppErrorBoundary>);
  expect(screen.getByRole('alert')).toHaveTextContent('页面暂时无法显示');
  expect(screen.getByRole('main')).toHaveFocus();
  expect(screen.queryByText(/private-provider-token/)).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '重新加载当前页面' }));
  expect(retry).toHaveBeenCalledOnce();
});

it('leaves healthy content available', () => {
  render(<AppErrorBoundary><p>研究内容</p></AppErrorBoundary>);
  expect(screen.getByText('研究内容')).toBeVisible();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
