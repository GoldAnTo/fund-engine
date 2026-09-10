import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FundClawApp } from "@/app/FundClawApp";

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("FundClawApp automatic local identity", () => {
  it("loads the research identity automatically without exposing a token entry point", async () => {
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/research-session")) {
        return jsonResponse({ tenant_id: "team-a", roles: [], subject_id: "alice" });
      }
      if (String(input).endsWith("/company-studies")) return jsonResponse({ studies: [] });
      return jsonResponse({ conversations: [] });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FundClawApp />);

    expect(await screen.findByText("研究员：alice")).toBeInTheDocument();
    expect(await screen.findByLabelText("公司名称")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /令牌|token/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("访问令牌")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/v1/research-session",
      "/api/v1/company-studies",
      "/api/v1/research-conversations",
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get("Authorization")).toBeNull();
    }
    expect(storageWrite.mock.calls.every(([key, value]) => key === "fundclaw.pending-idempotency.v1.probe" && value === "1")).toBe(true);
    expect(sessionStorage.getItem("fundclaw.pending-idempotency.v1.probe")).toBeNull();
  });

  it("reconfirms local identity after a page remount without browser credentials", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input).endsWith("/research-session")) {
        return jsonResponse({ tenant_id: "team-a", roles: [], subject_id: "alice" });
      }
      if (String(input).endsWith("/company-studies")) return jsonResponse({ studies: [] });
      return jsonResponse({ conversations: [] });
    });
    vi.stubGlobal("fetch", fetchMock);

    const firstMount = render(<FundClawApp />);
    await screen.findByLabelText("公司名称");
    firstMount.unmount();
    render(<FundClawApp />);

    expect(await screen.findByLabelText("公司名称")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(6);
    expect(screen.queryByRole("button", { name: /令牌|token/i })).not.toBeInTheDocument();
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get("Authorization")).toBeNull();
    }
  });

  it("explains unavailable local identity inline and retries without asking for a token", async () => {
    const user = userEvent.setup();
    let identityAvailable = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (!identityAvailable) return new Response(null, { status: 401 });
      if (String(input).endsWith("/research-session")) {
        return jsonResponse({ tenant_id: "team-a", roles: [], subject_id: "alice" });
      }
      if (String(input).endsWith("/company-studies")) return jsonResponse({ studies: [] });
      return jsonResponse({ conversations: [] });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FundClawApp />);

    expect(await screen.findByRole("alert")).toHaveTextContent("本地研究身份暂不可用");
    expect(screen.getByRole("alert")).toHaveTextContent("本地服务的身份配置");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /令牌|token/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("公司名称")).not.toBeInTheDocument();
    identityAvailable = true;
    await user.click(screen.getByRole("button", { name: "更新列表" }));

    expect(await screen.findByLabelText("公司名称")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(6));
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get("Authorization")).toBeNull();
    }
  });

  it("offers service configuration guidance when the local proxy is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 503 })));

    render(<FundClawApp />);

    expect(await screen.findByRole("alert")).toHaveTextContent("本地研究服务");
    expect(screen.getByRole("alert")).toHaveTextContent("身份配置");
    expect(screen.getAllByRole("button", { name: "更新列表" }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /令牌|token/i })).not.toBeInTheDocument();
    expect(screen.getByText("本地身份未就绪")).toBeInTheDocument();
  });

  it("can retry a missing stable subject after the local service is configured", async () => {
    const user = userEvent.setup();
    let subjectId: string | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/research-session")) {
        return jsonResponse({ tenant_id: "team-a", roles: [], subject_id: subjectId });
      }
      if (String(input).endsWith("/company-studies")) return jsonResponse({ studies: [] });
      return jsonResponse({ conversations: [] });
    }));

    render(<FundClawApp />);

    expect(await screen.findByRole("alert")).toHaveTextContent("本地服务尚未配置稳定研究主体");
    expect(screen.queryByLabelText("公司名称")).not.toBeInTheDocument();
    expect(screen.getByText("未配置研究主体")).toBeInTheDocument();
    subjectId = "alice";
    await user.click(screen.getByRole("button", { name: "更新列表" }));

    expect(await screen.findByLabelText("公司名称")).toBeInTheDocument();
  });
});
