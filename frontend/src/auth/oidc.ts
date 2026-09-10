import {
  UserManager,
  WebStorageStateStore,
  type User,
  type UserManagerSettings,
} from "oidc-client-ts";

export interface OidcTokenProvider {
  currentAccessToken(): Promise<string | null>;
  refreshAccessToken(): Promise<string | null>;
}

let manager: UserManager | null = null;
let tokenProviderOverride: OidcTokenProvider | null = null;
let refreshInFlight: Promise<string | null> | null = null;
const expiredListeners = new Set<() => void>();

function hasOidcConfiguration(): boolean {
  return Boolean(
    import.meta.env.VITE_OIDC_AUTHORITY?.trim()
    && import.meta.env.VITE_OIDC_CLIENT_ID?.trim(),
  );
}

function requiredEnvironment(name: string, value: string | undefined): string {
  const normalized = value?.trim();
  if (!normalized) throw new Error(`${name} is required`);
  return normalized;
}

function settings(): UserManagerSettings {
  const redirectUri =
    import.meta.env.VITE_OIDC_REDIRECT_URI?.trim()
    || `${window.location.origin}/auth/callback`;
  return {
    authority: requiredEnvironment(
      "VITE_OIDC_AUTHORITY",
      import.meta.env.VITE_OIDC_AUTHORITY,
    ),
    client_id: requiredEnvironment(
      "VITE_OIDC_CLIENT_ID",
      import.meta.env.VITE_OIDC_CLIENT_ID,
    ),
    redirect_uri: redirectUri,
    silent_redirect_uri:
      import.meta.env.VITE_OIDC_SILENT_REDIRECT_URI?.trim()
      || `${window.location.origin}/auth/silent-callback`,
    post_logout_redirect_uri:
      import.meta.env.VITE_OIDC_POST_LOGOUT_REDIRECT_URI?.trim()
      || window.location.origin,
    response_type: "code",
    scope: import.meta.env.VITE_OIDC_SCOPE?.trim() || "openid profile email",
    automaticSilentRenew: true,
    monitorSession: true,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  };
}

export function getOidcManager(): UserManager {
  manager ??= new UserManager(settings());
  return manager;
}

function token(user: User | null | undefined): string | null {
  return user?.access_token?.trim() || null;
}

function notifyAuthenticationExpired(): void {
  for (const listener of expiredListeners) listener();
}

export function onAuthenticationExpired(listener: () => void): () => void {
  expiredListeners.add(listener);
  return () => expiredListeners.delete(listener);
}

export const oidcTokenProvider: OidcTokenProvider = {
  async currentAccessToken() {
    if (!hasOidcConfiguration()) return null;
    const current = await getOidcManager().getUser();
    if (!current) return null;
    if (current.expired) return this.refreshAccessToken();
    return token(current);
  },
  async refreshAccessToken() {
    if (!hasOidcConfiguration()) return null;
    refreshInFlight ??= getOidcManager()
      .signinSilent()
      .then(token)
      .catch(() => null)
      .finally(() => { refreshInFlight = null; });
    return refreshInFlight;
  },
};

function withToken(headers: HeadersInit | undefined, accessToken: string | null): Headers {
  const next = new Headers(headers);
  if (accessToken) next.set("Authorization", `Bearer ${accessToken}`);
  else next.delete("Authorization");
  return next;
}

export async function fetchWithOidc(
  input: RequestInfo | URL,
  init: RequestInit = {},
  provider: OidcTokenProvider = tokenProviderOverride ?? oidcTokenProvider,
): Promise<Response> {
  const accessToken = await provider.currentAccessToken();
  const response = await fetch(input, {
    ...init,
    headers: withToken(init.headers, accessToken),
  });
  if (response.status !== 401) return response;

  const refreshed = await provider.refreshAccessToken();
  if (!refreshed) {
    notifyAuthenticationExpired();
    return response;
  }
  const retried = await fetch(input, {
    ...init,
    headers: withToken(init.headers, refreshed),
  });
  if (retried.status === 401) notifyAuthenticationExpired();
  return retried;
}

export function isOidcCallback(location: Location = window.location): boolean {
  const query = new URLSearchParams(location.search);
  return query.has("state") && (query.has("code") || query.has("error"));
}

export async function completeSilentOidcCallbackIfPresent(
  location: Location = window.location,
): Promise<boolean> {
  const configured =
    import.meta.env.VITE_OIDC_SILENT_REDIRECT_URI?.trim()
    || `${window.location.origin}/auth/silent-callback`;
  const callback = new URL(configured, window.location.origin);
  if (location.pathname !== callback.pathname || !isOidcCallback(location)) {
    return false;
  }
  await getOidcManager().signinSilentCallback();
  return true;
}

export function resetOidcForTests(): void {
  manager = null;
  tokenProviderOverride = null;
  refreshInFlight = null;
  expiredListeners.clear();
}

export function setOidcTokenProviderForTests(
  provider: OidcTokenProvider | null,
): void {
  tokenProviderOverride = provider;
}
