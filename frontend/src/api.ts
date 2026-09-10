import type { WorkbenchResponse } from "./types";

const API_BASE = "http://127.0.0.1:8000";

export async function fetchWorkbench(
  caseId: string,
  cutoff?: string
): Promise<WorkbenchResponse> {
  const url = new URL(
    `/api/research-cases/${caseId}/workbench`,
    API_BASE
  );
  if (cutoff) url.searchParams.set("cutoff", cutoff);
  const res = await fetch(url.toString());
  if (!res.ok) {
    throw new Error(`workbench fetch failed: ${res.status}`);
  }
  return (await res.json()) as WorkbenchResponse;
}
