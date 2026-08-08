import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { ReportResearchCreateScreen } from "../pages/prototype/ReportResearchCreateScreen";

function CreatedWorkbench() {
  const { caseId } = useParams();
  return <p>已进入研报工作台：{caseId}</p>;
}

function renderCreate() {
  return render(
    <MemoryRouter initialEntries={["/reports/new"]}>
      <Routes>
        <Route path="/reports/new" element={<ReportResearchCreateScreen />} />
        <Route path="/reports/:caseId" element={<CreatedWorkbench />} />
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

  it("creates pasted report research and opens the returned case workbench", async () => {
    const user = userEvent.setup();
    const create = vi.spyOn(adapter, "createReportResearch").mockResolvedValue({
      caseId: "report-paste-1", documentId: "document-paste-1", state: "ready_to_extract", needsTextOrPages: false, initialScopeVersion: 1,
    });
    renderCreate();

    await user.type(screen.getByLabelText("研报标题"), "AI 服务器产业链更新");
    await user.type(screen.getByLabelText("研报正文"), "核心观点：供应商甲将受益于订单增长。");
    await user.click(screen.getByRole("button", { name: "创建并开始自动研究" }));

    expect(create).toHaveBeenCalledWith(expect.objectContaining({
      inputKind: "pasted_text",
      title: "AI 服务器产业链更新",
      content: "核心观点：供应商甲将受益于订单增长。",
    }));
    expect(await screen.findByText("已进入研报工作台：report-paste-1")).toBeVisible();
  });

  it("submits web text and retains its publisher, publication time, and source URL", async () => {
    const user = userEvent.setup();
    const create = vi.spyOn(adapter, "createReportResearch").mockResolvedValue({
      caseId: "report-web-1", documentId: "document-web-1", state: "ready_to_extract", needsTextOrPages: false, initialScopeVersion: 1,
    });
    renderCreate();

    await user.selectOptions(screen.getByLabelText("研报输入方式"), "web_content");
    await user.type(screen.getByLabelText("研报标题"), "网页研报");
    await user.type(screen.getByLabelText("发布机构（可选）"), "测试券商");
    await user.type(screen.getByLabelText("发布时间（可选）"), "2026-08-08T09:30");
    await user.type(screen.getByLabelText("来源网址"), "https://research.example.test/report");
    await user.type(screen.getByLabelText("网页正文"), "观点：目标公司订单增长。");
    await user.click(screen.getByRole("button", { name: "创建并开始自动研究" }));

    expect(create).toHaveBeenCalledWith(expect.objectContaining({
      inputKind: "web_content", publisher: "测试券商", sourceUrl: "https://research.example.test/report",
      publishedAt: expect.stringMatching(/Z$/), content: "观点：目标公司订单增长。",
    }));
    expect(await screen.findByText("已进入研报工作台：report-web-1")).toBeVisible();
  });

  it("uses the PDF upload contract and shows a recoverable upload error", async () => {
    const user = userEvent.setup();
    const createPdf = vi.spyOn(adapter, "createReportResearchPdf")
      .mockRejectedValueOnce(new Error("PDF 解析服务暂不可用"))
      .mockResolvedValueOnce({ caseId: "report-pdf-1", documentId: "document-pdf-1", state: "ready_to_extract", needsTextOrPages: false, initialScopeVersion: 1 });
    renderCreate();

    await user.selectOptions(screen.getByLabelText("研报输入方式"), "pdf_upload");
    await user.type(screen.getByLabelText("研报标题"), "上传 PDF 研报");
    const file = new File(["%PDF-1.4 report"], "report.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("PDF 文件"), file);
    await user.click(screen.getByRole("button", { name: "创建并开始自动研究" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("PDF 解析服务暂不可用");
    await user.click(screen.getByRole("button", { name: "重试提交" }));
    expect(createPdf).toHaveBeenLastCalledWith(expect.objectContaining({ title: "上传 PDF 研报", file }));
    expect(await screen.findByText("已进入研报工作台：report-pdf-1")).toBeVisible();
  });

  it("keeps a PDF that needs pages in recoverable intake instead of opening an empty workbench", async () => {
    const user = userEvent.setup();
    const createPdf = vi.spyOn(adapter, "createReportResearchPdf").mockResolvedValue({
      caseId: "report-pdf-needs-text", documentId: "document-pdf-needs-text", state: "needs_text_or_pages", needsTextOrPages: true, initialScopeVersion: null,
    });
    const graph = vi.spyOn(adapter, "getReportWikiGraph");
    renderCreate();

    await user.selectOptions(screen.getByLabelText("研报输入方式"), "pdf_upload");
    await user.type(screen.getByLabelText("研报标题"), "扫描版 PDF 研报");
    await user.upload(screen.getByLabelText("PDF 文件"), new File(["%PDF-1.4"], "scan.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "创建并开始自动研究" }));

    expect(createPdf).toHaveBeenCalledOnce();
    expect(await screen.findByRole("status")).toHaveTextContent("原件已冻结，等待补充");
    expect(screen.getByRole("button", { name: "补充正文或页码" })).toBeVisible();
    expect(screen.getByRole("button", { name: "重新提交当前 PDF" })).toBeVisible();
    expect(screen.queryByText("已进入研报工作台：report-pdf-needs-text")).not.toBeInTheDocument();
    expect(graph).not.toHaveBeenCalled();
  });

  it("keeps text research in intake when no claims produced an initial scope", async () => {
    const user = userEvent.setup();
    vi.spyOn(adapter, "createReportResearch").mockResolvedValue({
      caseId: "report-no-claims", documentId: "document-no-claims", state: "ready_to_extract", needsTextOrPages: false, initialScopeVersion: null,
    });
    const graph = vi.spyOn(adapter, "getReportWikiGraph");
    renderCreate();

    await user.type(screen.getByLabelText("研报标题"), "只有资料描述的研报");
    await user.type(screen.getByLabelText("研报正文"), "资料描述：行业近期存在订单变化。");
    await user.click(screen.getByRole("button", { name: "创建并开始自动研究" }));

    expect(await screen.findByRole("status")).toHaveTextContent("原件已冻结，尚未解析主张");
    expect(screen.getByRole("button", { name: "修改内容后继续解析" })).toBeVisible();
    expect(screen.getByRole("button", { name: "重新提交当前内容" })).toBeVisible();
    expect(screen.queryByText("已进入研报工作台：report-no-claims")).not.toBeInTheDocument();
    expect(graph).not.toHaveBeenCalled();
  });
});
