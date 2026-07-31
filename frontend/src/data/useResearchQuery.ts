import { useEffect, useState } from "react";
import { PageStateError } from "../domain/types";

export interface ResearchQueryState<T> {
  data: T | null;
  error: PageStateError | null;
  loading: boolean;
  reload: () => void;
}

// Wraps a researchClient call into {data, error, loading} state and translates
// plain errors into PageStateError so pages can render offline / permission
// banners without parsing message strings.
export function useResearchQuery<T>(
  fn: () => Promise<T>,
  deps: unknown[]
): ResearchQueryState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<PageStateError | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof PageStateError) {
          setError(e);
        } else if (e && typeof e === "object" && "kind" in e) {
          setError(new PageStateError((e as { kind: string }).kind as never));
        } else {
          setError(new PageStateError("backend_unavailable", String(e)));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return {
    data,
    error,
    loading,
    reload: () => setTick((t) => t + 1),
  };
}