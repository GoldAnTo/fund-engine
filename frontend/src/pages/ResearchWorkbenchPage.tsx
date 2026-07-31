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

  useEffect(() => {
    fetchWorkbench(caseId)
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [caseId]);

  if (error) return <div className="error">加载失败：{error}</div>;
  if (!data) return <div className="loading">加载中…</div>;

  const selectedRecord =
    data.evidence_drawer_records.find((r) => r.link_id === selectedEvidenceId) ?? null;

  return (
    <div className="workbench">
      <AssessmentHeader data={data} />
      <div className="main">
        <EvidenceGraph
          data={data}
          onSelectEvidence={setSelectedEvidenceId}
          onSelectNode={setSelectedNodeId}
        />
        {selectedRecord && <EvidenceDrawer record={selectedRecord} />}
        <ExposurePanel data={data} selectedNodeId={selectedNodeId} />
      </div>
    </div>
  );
}
