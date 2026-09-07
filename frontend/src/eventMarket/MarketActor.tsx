import { createContext, useContext } from 'react';
export const MarketActorContext = createContext('');
export function useMarketActor() {
  const actor = useContext(MarketActorContext);
  if (!actor.trim()) throw new Error('请先填写市场研究操作人。');
  return actor;
}
