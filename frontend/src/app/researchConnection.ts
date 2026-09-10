export type ResearchConnectionEnvironment = {
  isDevelopment: boolean;
  apiUrl: string | undefined;
  backendUrl: string | undefined;
  oidcAuthority: string | undefined;
  oidcClientId: string | undefined;
};

export function researchClientMode(mockRequested: boolean): "http" | "mock" {
  return mockRequested ? "mock" : "http";
}

/**
 * Explain only an objectively incomplete local HTTP setup.  Runtime HTTP
 * failures remain generic because the browser cannot safely distinguish a
 * rejected credential from a missing Case or network failure.
 */
export function describeResearchConnection(
  environment: ResearchConnectionEnvironment,
): string | null {
  if (!environment.isDevelopment) return null;
  const hasApiRoute = Boolean(
    environment.apiUrl?.trim() || environment.backendUrl?.trim(),
  );
  const hasOidc = Boolean(
    environment.oidcAuthority?.trim() && environment.oidcClientId?.trim(),
  );
  if (hasApiRoute && hasOidc) return null;
  if (!hasApiRoute && !hasOidc) {
    return "本地真实数据连接尚未配置：设置 VITE_BACKEND_URL 或 VITE_RESEARCH_API_URL，并设置 VITE_OIDC_AUTHORITY 与 VITE_OIDC_CLIENT_ID 后重启前端。";
  }
  if (!hasApiRoute) {
    return "本地真实数据连接尚未配置后端：设置 VITE_BACKEND_URL 或 VITE_RESEARCH_API_URL 后重启前端。";
  }
  return "本地真实数据连接缺少登录配置：设置 VITE_OIDC_AUTHORITY 与 VITE_OIDC_CLIENT_ID 后重启前端。";
}

export const researchConnectionGuidance = describeResearchConnection({
  isDevelopment: import.meta.env.DEV,
  apiUrl: import.meta.env.VITE_RESEARCH_API_URL,
  backendUrl: import.meta.env.VITE_BACKEND_URL,
  oidcAuthority: import.meta.env.VITE_OIDC_AUTHORITY,
  oidcClientId: import.meta.env.VITE_OIDC_CLIENT_ID,
});
