import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const oidc = vi.hoisted(() => ({
  getUser: vi.fn(),
  signinSilent: vi.fn(),
  signinRedirect: vi.fn(),
  signinRedirectCallback: vi.fn(),
  signinSilentCallback: vi.fn(),
  signoutRedirect: vi.fn(),
  events: {
    addSilentRenewError: vi.fn(),
    removeSilentRenewError: vi.fn(),
    addUserUnloaded: vi.fn(),
    removeUserUnloaded: vi.fn(),
    addUserSignedOut: vi.fn(),
    removeUserSignedOut: vi.fn(),
  },
}));

vi.mock("oidc-client-ts", () => ({
  UserManager: vi.fn(() => oidc),
  WebStorageStateStore: vi.fn(() => ({ storage: "session" })),
}));

import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { RequireAuth } from "../auth/RequireAuth";
import {
  completeSilentOidcCallbackIfPresent,
  fetchWithOidc,
  resetOidcForTests,
  type OidcTokenProvider,
} from "../auth/oidc";
import { HttpResearchAdapter } from "../data/httpResearchAdapter";

const freshUser = {
  access_token: "fresh-token",
  expired: false,
  expires_in: 300,
  state: undefined,
};

function sessionResponse(status = 200) {
  return new Response(JSON.stringify(status === 200 ? {
    user_id: "00000000-0000-0000-0000-000000000001",
    display_name: "研究员甲",
    tenant_id: "team-a",
    roles: ["researcher"],
    issuer: "http://id.test/realms/research",
    expires_at: "2026-08-17T02:00:00Z",
  } : {
    error: { code: "permission_denied", message: "research permission required" },
  }), { status, headers: { "content-type": "application/json" } });
}

function Probe() {
  const auth = useAuth();
  return <div>
    <span>{auth.session?.display_name}</span>
    <button type="button" onClick={() => void auth.logout()}>退出登录</button>
  </div>;
}

function renderGuarded() {
  return render(<AuthProvider><RequireAuth><Probe /></RequireAuth></AuthProvider>);
}

describe("OIDC guarded startup", () => {
  beforeEach(() => {
    resetOidcForTests();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.stubEnv("VITE_OIDC_AUTHORITY", "http://id.test/realms/research");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "research-web");
    vi.stubEnv("VITE_OIDC_REDIRECT_URI", "http://localhost/auth/callback");
    vi.stubEnv("VITE_OIDC_SCOPE", "openid profile email");
    window.history.replaceState({}, "", "/events");
  });

  it("completes the authorization callback before loading the session and restores the return route", async () => {
    window.history.replaceState({}, "", "/auth/callback?code=code-1&state=state-1");
    oidc.signinRedirectCallback.mockResolvedValue({
      ...freshUser,
      state: { returnUrl: "/events/case-1/review" },
    });
    vi.stubGlobal("fetch", vi.fn(async () => sessionResponse()));

    renderGuarded();

    expect(await screen.findByText("研究员甲")).toBeInTheDocument();
    expect(oidc.signinRedirectCallback).toHaveBeenCalledOnce();
    expect(window.location.pathname).toBe("/events/case-1/review");
  });

  it("silently refreshes an expired user before the research session request", async () => {
    oidc.getUser.mockResolvedValue({ ...freshUser, access_token: "expired", expired: true, expires_in: 0 });
    oidc.signinSilent.mockResolvedValue(freshUser);
    const fetchSpy = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer fresh-token");
      return sessionResponse();
    });
    vi.stubGlobal("fetch", fetchSpy);

    renderGuarded();

    expect(await screen.findByText("研究员甲")).toBeInTheDocument();
    expect(oidc.signinSilent).toHaveBeenCalledOnce();
  });

  it("completes an iframe silent-renew callback through the dedicated callback", async () => {
    window.history.replaceState({}, "", "/auth/silent-callback?code=silent-1&state=state-1");

    await expect(completeSilentOidcCallbackIfPresent()).resolves.toBe(true);

    expect(oidc.signinSilentCallback).toHaveBeenCalledOnce();
    expect(oidc.signinRedirectCallback).not.toHaveBeenCalled();
  });

  it("shows an expired-session state when silent refresh cannot recover", async () => {
    oidc.getUser.mockResolvedValue({ ...freshUser, expired: true, expires_in: 0 });
    oidc.signinSilent.mockRejectedValue(new Error("login_required"));

    renderGuarded();

    expect(await screen.findByText("登录已过期")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新登录" }));
    expect(oidc.signinRedirect).toHaveBeenCalledWith(expect.objectContaining({
      state: { returnUrl: "/events" },
    }));
  });

  it("shows an explicit login error without loading Case data", async () => {
    oidc.getUser.mockRejectedValue(new Error("discovery unavailable"));
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    renderGuarded();

    expect(await screen.findByRole("alert")).toHaveTextContent("登录状态读取失败");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("logs out through the OIDC provider", async () => {
    oidc.getUser.mockResolvedValue(freshUser);
    vi.stubGlobal("fetch", vi.fn(async () => sessionResponse()));
    renderGuarded();
    await screen.findByText("研究员甲");

    await userEvent.click(screen.getByRole("button", { name: "退出登录" }));

    expect(oidc.signoutRedirect).toHaveBeenCalledWith();
  });

  it("shows an explicit error when the login redirect cannot start", async () => {
    oidc.getUser.mockResolvedValue(null);
    oidc.signinRedirect.mockRejectedValue(new Error("navigation blocked"));
    renderGuarded();

    await userEvent.click(await screen.findByRole("button", { name: "登录研究系统" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("登录状态读取失败");
  });

  it("shows an explicit error when the logout redirect cannot start", async () => {
    oidc.getUser.mockResolvedValue(freshUser);
    oidc.signoutRedirect.mockRejectedValue(new Error("provider unavailable"));
    vi.stubGlobal("fetch", vi.fn(async () => sessionResponse()));
    renderGuarded();
    await screen.findByText("研究员甲");

    await userEvent.click(screen.getByRole("button", { name: "退出登录" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("登录状态读取失败");
    expect(screen.queryByText("研究员甲")).not.toBeInTheDocument();
  });

  it("shows permission denied when the authenticated user cannot enter research", async () => {
    oidc.getUser.mockResolvedValue(freshUser);
    vi.stubGlobal("fetch", vi.fn(async () => sessionResponse(403)));

    renderGuarded();

    expect(await screen.findByRole("alert")).toHaveTextContent("没有研究系统权限");
  });

  it("removes Case content when a runtime request cannot refresh its token", async () => {
    oidc.getUser.mockResolvedValue(freshUser);
    vi.stubGlobal("fetch", vi.fn(async () => sessionResponse()));
    renderGuarded();
    await screen.findByText("研究员甲");

    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 401 })));
    await act(async () => {
      await fetchWithOidc("/api/v1/research-cases", {}, {
        currentAccessToken: async () => "expired-token",
        refreshAccessToken: async () => null,
      });
    });

    expect(await screen.findByText("登录已过期")).toBeInTheDocument();
    expect(screen.queryByText("研究员甲")).not.toBeInTheDocument();
  });
});

describe("request token replacement", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("retries one request with the refreshed access token", async () => {
    const tokens: OidcTokenProvider = {
      currentAccessToken: vi.fn().mockResolvedValue("old-token"),
      refreshAccessToken: vi.fn().mockResolvedValue("new-token"),
    };
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "authentication_required" } }), { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: null, has_more: false }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    const adapter = new HttpResearchAdapter({
      baseUrl: "http://api.test/api/v1",
      tokenProvider: tokens,
    });

    await act(async () => {
      await expect(adapter.getCaseSummaries()).resolves.toEqual([]);
    });

    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(new Headers(fetchSpy.mock.calls[0][1]?.headers).get("Authorization")).toBe("Bearer old-token");
    expect(new Headers(fetchSpy.mock.calls[1][1]?.headers).get("Authorization")).toBe("Bearer new-token");
    expect(tokens.refreshAccessToken).toHaveBeenCalledOnce();
  });
});
