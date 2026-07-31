import { useEffect, useState } from "react";
import { fetchWorkbench } from "../api";
import type { WorkbenchResponse } from "../types";
import { AssessmentHeader } from "../components/AssessmentHeader";
import { EvidenceGraph } from "../components/EvidenceGraph";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { ExposurePanel } from "../components/ExposurePanel";

export function ResearchWorkbenchPage({ caseId }: { caseId: string }) {
  const [data, setData] = useState<WorkbenchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [cutoff, setCutoff] = useState<string | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    fetchWorkbench(caseId, cutoff)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [caseId, cutoff]);

  if (error) return <div className="error">加载失败：{error}</div>;
  if (!data) return <div className="loading">加载中…</div>;

  const selectedRecord =
    data.evidence_drawer_records.find((r) => r.link_id === selectedEvidenceId) ?? null;

  return (
    <div className="workbench">
      <AssessmentHeader data={data} />
      <div className="time-travel-bar">
        <label htmlFor="cutoff-date">⏱ 时间旅行</label>
        <input
          id="cutoff-date"
          type="date"
          value={cutoff ?? ""}
          onChange={(e) => {
            const v = e.target.value;
            setCutoff(v ? v : undefined);
          }}
        />
        {cutoff && (
          <>
            <span className="time-travel-flag" data-testid="time-travel-flag">
              ⏱ 时间旅行至 {cutoff}
            </span>
            <button type="button" onClick={() => setCutoff(undefined)}>
              回到当前
            </button>
          </>
        )}
      </div>
      <div className="main">
        <EvidenceGraph
          data={data}
          onSelectEvidence={setSelectedEvidenceId}
          onSelectNode={setSelectedNodeId}
        />
        {selectedRecord && (
          <EvidenceDrawer
            record={selectedRecord}
            allRecords={data.evidence_drawer_records}
          />
        )}
        <ExposurePanel data={data} selectedNodeId={selectedNodeId} />
      </div>
    </div>
  );
}
