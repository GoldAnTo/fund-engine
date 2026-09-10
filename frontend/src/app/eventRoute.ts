export type SelectedEventRoute = {
  caseId: string;
  isOverview: boolean;
};

function decodePathSegment(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

export function parseSelectedEventRoute(pathname: string): SelectedEventRoute | null {
  const segments = pathname.split("/").filter(Boolean);
  if (segments[0] !== "events" || segments.length < 2) {
    return null;
  }
  const caseId = decodePathSegment(segments[1]);
  if (caseId === null || caseId.toLowerCase() === "new") return null;
  return {
    caseId,
    isOverview: segments.length === 2,
  };
}
