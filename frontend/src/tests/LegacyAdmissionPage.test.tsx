import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { LegacyAdmissionPage } from "../features/events/LegacyAdmissionPage";
import { resetResearchOsApi, setResearchOsApi, type ResearchOsApi } from "../app/researchOsApi";

describe("legacy Case admission", () => {
  afterEach(() => resetResearchOsApi());

  it("explains admission requirements before appending the explicit admission", async () => {
    const admitLegacyCase = vi.fn().mockResolvedValue({
      case_id: "case-legacy",
      tenant_id: "team-a",
      initial_document_version_id: "doc-legacy",
      admitted_by: "human:case-administrator",
      reason: "迁移记录已核验",
      admitted_at: "2026-08-10T00:00:00Z",
    });
    const api = {
      session: vi.fn().mockResolvedValue({ tenant_id: "team-a", display_name: "研究管理员", roles: ["case_administrator"] }),
      legacyAdmissionQueue: vi.fn().mockResolvedValue({
        items: [{
          case_id: "case-legacy",
          event_title: "历史订单事件",
          created_at: "2026-08-01T00:00:00Z",
          documents: [{ document_version_id: "doc-legacy", title: "冻结公告", source_url: "https://example.test/original", available_at: "2026-08-01T00:00:00Z" }],
        }],
      }),
      admitLegacyCase,
    } as unknown as ResearchOsApi;
    setResearchOsApi(api);
    const user = userEvent.setup();

    render(<MemoryRouter><LegacyAdmissionPage /></MemoryRouter>);

    await screen.findByRole("heading", { name: "历史 Case 准入" });
    expect(screen.getAllByText(/研究管理员/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "确认并准入此 Case" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("准入前还需：填写准入依据");

    await user.type(screen.getByLabelText("历史订单事件 的准入依据"), "迁移记录已核验");
    await user.click(screen.getByRole("button", { name: "确认并准入此 Case" }));

    expect(admitLegacyCase).toHaveBeenCalledWith("case-legacy", {
      initial_document_version_id: "doc-legacy",
      reason: "迁移记录已核验",
    });
    expect(await screen.findByRole("status")).toHaveTextContent("已准入「历史订单事件」");
  });
});
