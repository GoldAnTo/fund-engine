import { useEffect, useRef, useState } from "react";

import { researchClient } from "../../data/researchClient";
import {
  automaticResearchPollDelay,
  normalizeAutomaticResearchView,
  type AutomaticResearchView,
} from "../../domain/automaticResearch";

type LoadError = "unavailable" | "invalid";
type RetryStatus = "idle" | "running" | "error";

export type AutomaticResearchProgressState =
  | { kind: "missing-case" }
  | { kind: "loading" }
  | { kind: "load-error"; reason: LoadError; reading: boolean }
  | {
    kind: "ready";
    caseId: string;
    view: AutomaticResearchView;
    reading: boolean;
    transientError: boolean;
    retryStatus: RetryStatus;
  };

export type AutomaticResearchProgress = {
  state: AutomaticResearchProgressState;
  reread: () => void;
  retry: () => Promise<void>;
};

class InvalidAutomaticResearchProjectionError extends Error {}

function isActive(view: AutomaticResearchView): boolean {
  return view.status === "queued" || view.status === "running";
}

function queuedRetryView(
  previous: AutomaticResearchView,
  runId: string,
): AutomaticResearchView {
  return {
    ...previous,
    runId,
    status: "queued",
    stages: previous.stages.map((stage) => ({
      ...stage,
      status: "pending",
      summary: `等待${stage.label}`,
      startedAt: null,
      completedAt: null,
    })),
    stats: { sourceCount: 0, admittedEvidenceCount: 0, skippedCount: 0, durationSeconds: 0 },
    recentActivity: ["新的自动研究已排队"],
    exceptions: [],
    failureReason: null,
    result: null,
  };
}

export function useAutomaticResearchProgress(
  caseId: string | undefined,
): AutomaticResearchProgress {
  const [view, setView] = useState<AutomaticResearchView | null>(null);
  const [loading, setLoading] = useState(Boolean(caseId));
  const [loadError, setLoadError] = useState<LoadError | null>(null);
  const [transientError, setTransientError] = useState(false);
  const [retryStatus, setRetryStatus] = useState<RetryStatus>("idle");
  const [reading, setReading] = useState(false);
  const [pollVersion, setPollVersion] = useState(0);
  const mountedRef = useRef(false);
  const requestTokenRef = useRef(0);
  const flowTokenRef = useRef(0);
  const seedViewRef = useRef<AutomaticResearchView | null>(null);
  const activeReadRef = useRef<Promise<AutomaticResearchView> | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestTokenRef.current += 1;
      flowTokenRef.current += 1;
    };
  }, []);

  useEffect(() => {
    const flowToken = ++flowTokenRef.current;
    let timer: number | null = null;
    const seed = seedViewRef.current?.caseId === caseId ? seedViewRef.current : null;
    seedViewRef.current = null;
    let lastView = seed;
    let consecutiveFailures = 0;
    setView(seed);
    setLoadError(null);
    setTransientError(false);
    setRetryStatus("idle");
    setLoading(Boolean(caseId) && !seed);

    if (!caseId) return undefined;

    const load = async () => {
      const existingRead = activeReadRef.current;
      if (existingRead) {
        try {
          await existingRead;
        } catch {
          // The owning flow presents the failure. A newer flow waits so GETs stay serialized.
        }
        if (!mountedRef.current || flowToken !== flowTokenRef.current) return;
      }
      const requestToken = ++requestTokenRef.current;
      const request = researchClient.getAutomaticResearch(caseId);
      activeReadRef.current = request;
      setReading(true);
      try {
        const response = await request;
        const nextView = normalizeAutomaticResearchView(response);
        if (!nextView) throw new InvalidAutomaticResearchProjectionError();
        if (
          !mountedRef.current
          || flowToken !== flowTokenRef.current
          || requestToken !== requestTokenRef.current
        ) return;
        lastView = nextView;
        consecutiveFailures = 0;
        setView(nextView);
        setLoadError(null);
        setTransientError(false);
        setLoading(false);
        if (isActive(nextView)) {
          timer = window.setTimeout(
            () => { void load(); },
            automaticResearchPollDelay(0),
          );
        }
      } catch (error) {
        if (
          !mountedRef.current
          || flowToken !== flowTokenRef.current
          || requestToken !== requestTokenRef.current
        ) return;
        setLoading(false);
        if (error instanceof InvalidAutomaticResearchProjectionError) {
          setView(null);
          setLoadError("invalid");
          setTransientError(false);
          return;
        }
        if (lastView && isActive(lastView)) {
          consecutiveFailures += 1;
          setView(lastView);
          setLoadError(null);
          setTransientError(true);
          timer = window.setTimeout(
            () => { void load(); },
            automaticResearchPollDelay(consecutiveFailures),
          );
          return;
        }
        setView(null);
        setLoadError("unavailable");
        setTransientError(false);
      } finally {
        if (activeReadRef.current === request) activeReadRef.current = null;
        if (
          mountedRef.current
          && flowToken === flowTokenRef.current
          && requestToken === requestTokenRef.current
        ) setReading(false);
      }
    };

    void load();
    return () => {
      flowTokenRef.current += 1;
      requestTokenRef.current += 1;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [caseId, pollVersion]);

  function reread() {
    if (!caseId || reading) return;
    if (view && transientError && isActive(view)) seedViewRef.current = view;
    else if (!loadError) return;
    setPollVersion((version) => version + 1);
  }

  async function retry() {
    if (!caseId || retryStatus === "running" || view?.status !== "failed") return;
    const requestToken = ++requestTokenRef.current;
    setRetryStatus("running");
    try {
      const started = await researchClient.retryAutomaticResearch(caseId);
      if (!mountedRef.current || requestToken !== requestTokenRef.current) return;
      const queued = queuedRetryView(view, started.runId);
      seedViewRef.current = queued;
      setView(queued);
      setRetryStatus("idle");
      setPollVersion((version) => version + 1);
    } catch {
      if (!mountedRef.current || requestToken !== requestTokenRef.current) return;
      setRetryStatus("error");
    }
  }

  if (!caseId) return { state: { kind: "missing-case" }, reread, retry };
  if (loading) return { state: { kind: "loading" }, reread, retry };
  if (loadError || !view) {
    return {
      state: { kind: "load-error", reason: loadError || "unavailable", reading },
      reread,
      retry,
    };
  }
  return {
    state: { kind: "ready", caseId, view, reading, transientError, retryStatus },
    reread,
    retry,
  };
}
