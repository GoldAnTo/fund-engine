import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { parseRecoveryRouteState, ReportResearchCreateScreen } from "../pages/prototype/ReportResearchCreateScreen";

function IntakeRoute() {
  const { caseId } = useParams();
  return <p>已进入资料接入：{caseId}</p>;
}

function CreateRoute() {
  const location = useLocation();
  return <><ReportResearchCreateScreen /><output data-testid="location">{`${location.pathname}${location.search}`}</output></>;
}

function renderCreate(initialEntry = "/reports/new") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/reports/new" element={<CreateRoute />} />
        <Route path="/reports/:caseId/intake" element={<IntakeRoute />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ReportResearchCreateScreen", () => {
  let adapter: MockResearchAdapter;

  beforeEach(() => {
    adapter = new MockResearchAdapter();
    setResearchClient(adapter);
  });
  afterEach(() => resetResearchClient());

  it("clears a complete recovery target and URL before creating new material", async () => {
    const user = userEvent.setup();
    const create = vi.spyOn(adapter, "createReportResearch");
    const supplement = vi.spyOn(adapter, "supplementReportResearch");
    renderCreate("/reports/new?resume_case_id=old-case&resume_document_id=old-doc&resume_input_kind=pdf_upload&resume_reason=needs_text_or_pages");

    expect(screen.getByRole("button", { name: "继续补充原 Case" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "放弃恢复并新建资料" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/reports/new");

    await user.type(screen.getByLabelText("研报标题"), "新研报");
    await user.type(screen.getByLabelText("研报正文"), "观点：订单增长。");
    await user.type(screen.getByLabelText("来源授权合同版本 ID"), "mock-source-contract-v1");
    await user.click(screen.getByRole("button", { name: "冻结资料并进入下一步" }));

    expect(create).toHaveBeenCalledOnce();
    expect(supplement).not.toHaveBeenCalled();
    expect(await screen.findByText("已进入资料接入：report-pasted_text-mock")).toBeVisible();
  });

  it("continues a complete recovery target as a separately frozen supplement", async () => {
    const user = userEvent.setup();
    const supplement = vi.spyOn(adapter, "supplementReportResearch");
    renderCreate("/reports/new?resume_case_id=old-case&resume_document_id=old-doc&resume_input_kind=pdf_upload&resume_reason=needs_text_or_pages");

    await user.click(screen.getByRole("button", { name: "继续补充原 Case" }));
    await user.type(screen.getByLabelText("研报正文"), "补充的可定位正文。");
    await user.type(screen.getByLabelText("来源授权合同版本 ID"), "mock-source-contract-v1");
    await user.click(screen.getByRole("button", { name: "冻结补充内容并进入下一步" }));

    expect(supplement).toHaveBeenCalledWith({
      caseId: "old-case", documentId: "old-doc", content: "补充的可定位正文。",
      pageReference: undefined, sourceContractId: "mock-source-contract-v1",
    });
    expect(await screen.findByText("已进入资料接入：old-case")).toBeVisible();
  });

  it("rejects malformed recovery query instead of silently supplementing a case", async () => {
    renderCreate("/reports/new?resume_case_id=old-case&resume_document_id=old-doc&resume_input_kind=unknown&resume_reason=needs_text_or_pages");
    expect(screen.queryByRole("button", { name: "继续补充原 Case" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "冻结资料并进入下一步" })).toBeVisible();
  });

  it("rejects duplicate and unknown resume parameters instead of picking one target", () => {
    expect(parseRecoveryRouteState(new URLSearchParams("resume_case_id=case-a&resume_case_id=case-b&resume_document_id=doc-a&resume_input_kind=pdf_upload&resume_reason=needs_supplement"))).toBeNull();
    expect(parseRecoveryRouteState(new URLSearchParams("resume_case_id=case-a&resume_document_id=doc-a&resume_input_kind=pdf_upload&resume_reason=needs_supplement&resume_actor=other"))).toBeNull();
  });
});
