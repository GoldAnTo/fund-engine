import { Navigate, useLocation, useParams } from "react-router-dom";

/** Keeps bookmarked prototype routes from reopening an unscoped research view. */
export function LegacyEventRedirect() {
  const location = useLocation();
  const params = useParams<{ caseId?: string }>();
  const caseId = params.caseId ?? new URLSearchParams(location.search).get("caseId");
  return <Navigate to={caseId ? `/events/${encodeURIComponent(caseId)}/basis` : "/events"} replace />;
}
