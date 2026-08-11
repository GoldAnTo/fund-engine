export type ResearchConnectionEnvironment = {
  isDevelopment: boolean;
  apiUrl: string | undefined;
  backendUrl: string | undefined;
  bearerToken: string | undefined;
  proxyBearerConfigured?: boolean;
};

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
  const hasBearerToken = Boolean(
    environment.bearerToken?.trim() || environment.proxyBearerConfigured,
  );
  if (hasApiRoute && hasBearerToken) return null;
  if (!hasApiRoute && !hasBearerToken) {
    return "本地真实数据连接尚未配置：设置 VITE_BACKEND_URL 或 VITE_RESEARCH_API_URL，并设置 RESEARCH_BEARER_TOKEN 后重启前端。";
  }
  if (!hasApiRoute) {
    return "本地真实数据连接尚未配置后端：设置 VITE_BACKEND_URL 或 VITE_RESEARCH_API_URL 后重启前端。";
  }
  return "本地真实数据连接缺少租户凭据：设置 RESEARCH_BEARER_TOKEN 后重启前端。";
}

export const researchConnectionGuidance = describeResearchConnection({
  isDevelopment: import.meta.env.DEV,
  apiUrl: import.meta.env.VITE_RESEARCH_API_URL,
  backendUrl: import.meta.env.VITE_BACKEND_URL,
  bearerToken: import.meta.env.VITE_RESEARCH_BEARER_TOKEN,
  proxyBearerConfigured:
    import.meta.env.VITE_RESEARCH_PROXY_AUTH_CONFIGURED === "true",
});
