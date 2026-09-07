import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { EvidenceReviewPanel } from '@/workbench/EvidenceReviewPanel';
import { validEvidenceQueue } from '@/data/evidenceReviewApi';
const caseId = '11111111-1111-4111-8111-111111111111';
const proposalId = '22222222-2222-4222-8222-222222222222';
const statementId = '33333333-3333-4333-8333-333333333333';
const row = { case_id: caseId, proposal_id: proposalId, proposal_version: 3, status: 'pending', proposed_at: '2026-09-01T00:00:00Z', thesis_id: statementId, statement_id: statementId, span_id: statementId, document_version_id: statementId, thesis_statement: '订单增长', statement_text: '订单增加', verbatim_text: '本季订单同比增加。', source_title: '季度公告', source_status: 'accessible', source_status_reason: '来源已冻结', can_accept: true, ai_role: 'supports', ai_reason: '订单数据支持需求增长', ai_scope: { period: '2026Q2' }, locator: { page: 3 }, document_source_url: 'https://example.com/report', document_published_at: null, available_at: '2026-09-01T00:00:00Z' };
const queue = (item = row) => ({ items: [item], summary: { total: 1, reviewed: 0, pending: 1, invalid_source: 0, current_round: 1, next_action: null } });
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
it('rejects contradictory source admission flags', () => {
  expect(validEvidenceQueue(queue({ ...row, source_status: 'restricted', can_accept: true }), caseId)).toBe(false);
});
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });
async function open() { await userEvent.click(screen.getByRole('button', { name: '查看待审核证据' })); return screen.findByRole('article', { name: '季度公告' }); }
it('requires an explicit human decision and retains reason and idempotency key after uncertain failure', async () => {
  const posts: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => { if (init.method === 'POST') { posts.push(init); return Promise.resolve(response({}, 503)); } return Promise.resolve(response(queue())); }));
  render(<EvidenceReviewPanel caseId={caseId} actor="研究员" onRefresh={() => {}} />);
  const article = await open();
  expect(within(article).getByText('本季订单同比增加。')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '提交审核决定' })).toBeDisabled();
  await userEvent.selectOptions(screen.getByLabelText('审核结论'), 'confirmed');
  await userEvent.type(screen.getByLabelText('审核理由'), '已核对原文及范围');
  await userEvent.click(screen.getByRole('button', { name: '提交审核决定' }));
  await screen.findByRole('alert');
  expect(screen.getByLabelText('审核理由')).toHaveValue('已核对原文及范围');
  await userEvent.click(screen.getByRole('button', { name: '提交审核决定' }));
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(JSON.parse(posts[0]!.body as string)).toEqual({ outcome: 'confirmed', reason: '已核对原文及范围', reviewer_id: '研究员', expected_version: 3 });
  expect(posts[0]!.headers).toEqual(posts[1]!.headers);
});
it('blocks acceptance and modification of inadmissible sources but allows rejection', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(response(queue({ ...row, can_accept: false, source_status: 'invalid' })))));
  render(<EvidenceReviewPanel caseId={caseId} actor="研究员" onRefresh={() => {}} />); await open();
  expect(screen.getByRole('option', { name: '接受 AI 提议' })).toBeDisabled();
  expect(screen.getByRole('option', { name: '修改后接受' })).toBeDisabled();
  await userEvent.selectOptions(screen.getByLabelText('审核结论'), 'rejected');
  await userEvent.type(screen.getByLabelText('审核理由'), '来源不可核对');
  expect(screen.getByRole('button', { name: '提交审核决定' })).toBeEnabled();
});
it('fails closed on a mismatched case', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(response(queue({ ...row, case_id: proposalId })))));
  render(<EvidenceReviewPanel caseId={caseId} actor="研究员" onRefresh={() => {}} />);
  await userEvent.click(screen.getByRole('button', { name: '查看待审核证据' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('格式不正确');
  expect(screen.queryByLabelText('审核结论')).not.toBeInTheDocument();
});
it('submits human selected replacement and refreshes only after matching published response', async () => {
  const refresh = vi.fn(); const posts: object[] = [];
  vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => {
    if (init.method !== 'POST') return Promise.resolve(response(queue()));
    const body = JSON.parse(init.body as string); posts.push(body);
    return Promise.resolve(response({ id: statementId, proposal_id: proposalId, outcome: body.outcome, reason: body.reason, reviewer_id: body.reviewer_id, expected_proposal_version: 3, decided_at: '2026-09-06T00:00:00Z', published_entity_id: statementId }));
  }));
  render(<EvidenceReviewPanel caseId={caseId} actor="研究员" onRefresh={refresh} />); await open();
  await userEvent.selectOptions(screen.getByLabelText('审核结论'), 'modified');
  await userEvent.type(screen.getByLabelText('审核理由'), '仅作为背景');
  expect(screen.getByRole('button', { name: '提交审核决定' })).toBeDisabled();
  await userEvent.selectOptions(screen.getByLabelText('人工证据角色'), 'contextualizes');
  await userEvent.type(screen.getByLabelText('适用范围'), '仅适用于第二季度');
  await userEvent.click(screen.getByRole('button', { name: '提交审核决定' }));
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  expect(posts[0]).toMatchObject({ replacement_payload: { source_statement_id: statementId, role: 'contextualizes', reason: '仅作为背景', scope: { description: '仅适用于第二季度' } } });
});
it('preserves drafts after version conflicts and refuses mismatched success acknowledgements', async () => {
  let status = 409; const refresh = vi.fn();
  vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => Promise.resolve(init.method === 'POST' ? response({ id: statementId, proposal_id: statementId, outcome: 'rejected', reason: '无效来源', reviewer_id: '研究员', expected_proposal_version: 3, decided_at: '2026-09-06T00:00:00Z', published_entity_id: null }, status) : response(queue()))));
  render(<EvidenceReviewPanel caseId={caseId} actor="研究员" onRefresh={refresh} />); await open();
  await userEvent.selectOptions(screen.getByLabelText('审核结论'), 'rejected'); await userEvent.type(screen.getByLabelText('审核理由'), '无效来源');
  await userEvent.click(screen.getByRole('button', { name: '提交审核决定' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('状态已变化');
  expect(screen.getByLabelText('审核理由')).toHaveValue('无效来源'); status = 200;
  await userEvent.click(screen.getByRole('button', { name: '提交审核决定' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('格式不正确'); expect(refresh).not.toHaveBeenCalled();
});
it('never carries loaded source or draft into another case', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(response(queue()))));
  const view = render(<EvidenceReviewPanel caseId={caseId} actor="研究员" onRefresh={() => {}} />); await open();
  await userEvent.type(screen.getByLabelText('审核理由'), '仅属于第一案例');
  view.rerender(<EvidenceReviewPanel caseId={proposalId} actor="研究员" onRefresh={() => {}} />);
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('审核理由')).not.toBeInTheDocument();
});
