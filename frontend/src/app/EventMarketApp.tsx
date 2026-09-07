import { useEffect, useState } from 'react';
import { BrowserRouter, Route, Routes, useParams } from 'react-router-dom';
import { MarketExpressionContent } from '@/eventMarket/MarketExpressionContent';
import { MarketStockProfile, MarketFundProfile } from '@/eventMarket/MarketInstrumentProfiles';
import { request } from '@/data/researchApi';
import '@/eventMarket/market.css';
import { MarketActorContext } from '@/eventMarket/MarketActor';
function MarketPage({ kind }: { kind: 'market' | 'stock' | 'fund' }) {
  const { caseId = '', instrumentId = '' } = useParams();
  const [theses, setTheses] = useState<{ id: string; statement: string }[] | null>(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [actorDraft, setActorDraft] = useState('');
  const [actor, setActor] = useState('');
  useEffect(() => {
    const controller = new AbortController(); setTheses(null); setError('');
    request<{ confirmed_factors: { id: string; statement: string }[] }>(`/research-cases/${encodeURIComponent(caseId)}/monitor`, controller.signal, (v) => {
      if (!v || typeof v !== 'object') return false;
      const factors = (v as { confirmed_factors?: unknown }).confirmed_factors;
      return Array.isArray(factors) && factors.every((f) => f && typeof f === 'object' && typeof f.id === 'string' && typeof f.statement === 'string');
    }, undefined, '/api/v1').then((data) => { if (!controller.signal.aborted) setTheses(data.confirmed_factors); }).catch((err) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '读取研究范围失败。'); });
    return () => controller.abort();
  }, [caseId, revision]);
  return <div className="event-market-app"><a href={`/?caseId=${encodeURIComponent(caseId)}`}>返回研究工作台</a>
    <MarketActorContext.Provider value={actor}><main><h1>市场研究与验证</h1><p>从已审核来源登记关键因素，核对预测与后续实际值，追踪公司、股票及基金关联。</p>
      {error ? <><p role="alert">{error}</p><button onClick={() => setRevision((r) => r + 1)}>重新读取研究范围</button></> : theses === null ? <p role="status">正在读取研究范围…</p> : !actor ? <form onSubmit={(e) => { e.preventDefault(); if (actorDraft.trim()) setActor(actorDraft.trim()); }}><label>市场研究操作人<input value={actorDraft} maxLength={128} onChange={(e) => setActorDraft(e.target.value)} /></label><button disabled={!actorDraft.trim()}>进入市场研究</button></form> : kind === 'stock' ? <MarketStockProfile caseId={caseId} stockId={instrumentId} /> : kind === 'fund' ? <MarketFundProfile caseId={caseId} fundId={instrumentId} /> : <MarketExpressionContent caseId={caseId} theses={theses} />}
    </main></MarketActorContext.Provider></div>;
}
export default function EventMarketApp() {
  return <BrowserRouter><Routes>
    <Route path="/events/:caseId/market" element={<MarketPage key="market" kind="market" />} />
    <Route path="/events/:caseId/stocks/:instrumentId" element={<MarketPage key="stock" kind="stock" />} />
    <Route path="/events/:caseId/funds/:instrumentId" element={<MarketPage key="fund" kind="fund" />} />
    <Route path="*" element={<a href="/">返回研究工作台</a>} />
  </Routes></BrowserRouter>;
}
