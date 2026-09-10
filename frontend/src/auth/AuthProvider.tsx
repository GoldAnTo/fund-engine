import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { User } from "oidc-client-ts";

import type { ResearchSession } from "../app/researchOsApi";
import {
  fetchWithOidc,
  getOidcManager,
  isOidcCallback,
  onAuthenticationExpired,
  type OidcTokenProvider,
} from "./oidc";

type AuthState =
  | { status: "loading"; session: null; reason?: undefined }
  | { status: "authenticated"; session: ResearchSession; reason?: undefined }
  | { status: "unauthenticated"; session: null; reason: "required" | "expired" }
  | { status: "permission_denied"; session: null; reason?: undefined }
  | { status: "error"; session: null; reason?: undefined };

export type AuthContextValue = AuthState & {
  login(): Promise<void>;
  logout(): Promise<void>;
  retry(): void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function currentRoute(): string {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function returnRoute(user: User): string {
  const state = user.state;
  if (state && typeof state === "object" && "returnUrl" in state) {
    const value = (state as { returnUrl?: unknown }).returnUrl;
    if (typeof value === "string" && value.startsWith("/") && !value.startsWith("//")) {
      return value;
    }
  }
  return "/events";
}

function providerFor(initial: User): OidcTokenProvider {
  let current = initial;
  return {
    async currentAccessToken() {
      return current.access_token?.trim() || null;
    },
    async refreshAccessToken() {
      const refreshed = await getOidcManager().signinSilent();
      if (!refreshed) return null;
      current = refreshed;
      return refreshed.access_token?.trim() || null;
    },
  };
}

async function loadResearchSession(user: User): Promise<Response> {
  const baseUrl = (import.meta.env.VITE_RESEARCH_API_URL || "/api/v1").replace(/\/$/, "");
  return fetchWithOidc(
    `${baseUrl}/research-session`,
    { credentials: "include", headers: { Accept: "application/json" } },
    providerFor(user),
  );
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading", session: null });
  const [attempt, setAttempt] = useState(0);

  useEffect(
    () => onAuthenticationExpired(
      () => setState({ status: "unauthenticated", session: null, reason: "expired" }),
    ),
    [],
  );

  useEffect(() => {
    try {
      const events = getOidcManager().events;
      const expire = () => {
        setState({ status: "unauthenticated", session: null, reason: "expired" });
      };
      events.addSilentRenewError(expire);
      events.addUserUnloaded(expire);
      events.addUserSignedOut(expire);
      return () => {
        events.removeSilentRenewError(expire);
        events.removeUserUnloaded(expire);
        events.removeUserSignedOut(expire);
      };
    } catch {
      return;
    }
  }, []);

  useEffect(() => {
    let active = true;
    const start = async () => {
      setState({ status: "loading", session: null });
      try {
        const oidc = getOidcManager();
        const callback = isOidcCallback();
        let user = callback
          ? await oidc.signinRedirectCallback()
          : await oidc.getUser();
        if (!user) {
          if (active) setState({ status: "unauthenticated", session: null, reason: "required" });
          return;
        }
        if (callback) window.history.replaceState({}, "", returnRoute(user));
        if (user.expired) {
          try {
            user = await oidc.signinSilent();
          } catch {
            if (active) setState({ status: "unauthenticated", session: null, reason: "expired" });
            return;
          }
        }
        if (!user) {
          if (active) setState({ status: "unauthenticated", session: null, reason: "expired" });
          return;
        }
        const response = await loadResearchSession(user);
        if (!active) return;
        if (response.status === 401) {
          setState({ status: "unauthenticated", session: null, reason: "expired" });
          return;
        }
        if (response.status === 403) {
          setState({ status: "permission_denied", session: null });
          return;
        }
        if (!response.ok) throw new Error(`research session failed (${response.status})`);
        setState({
          status: "authenticated",
          session: await response.json() as ResearchSession,
        });
      } catch {
        if (active) setState({ status: "error", session: null });
      }
    };
    void start();
    return () => { active = false; };
  }, [attempt]);

  const login = useCallback(async () => {
    try {
      await getOidcManager().signinRedirect({
        state: { returnUrl: currentRoute() },
      });
    } catch {
      setState({ status: "error", session: null });
    }
  }, []);
  const logout = useCallback(async () => {
    try {
      await getOidcManager().signoutRedirect();
    } catch {
      setState({ status: "error", session: null });
    }
  }, []);
  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  const value = useMemo<AuthContextValue>(
    () => ({ ...state, login, logout, retry }),
    [state, login, logout, retry],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
