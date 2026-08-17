import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";

import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import {
  AUTOMATIC_RESEARCH_ENTRY_COLORS,
  EventCreatePage,
} from "../features/events/EventCreatePage";

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function LeaveableCreatePage() {
  const navigate = useNavigate();
  return (
    <>
      <EventCreatePage />
      <button type="button" onClick={() => navigate("/away")}>离开创建页</button>
    </>
  );
}

function renderCreatePage() {
  return render(
    <MemoryRouter initialEntries={["/events/new"]}>
      <Routes>
        <Route path="/events/new" element={<EventCreatePage />} />
        <Route
          path="/events/:caseId/automatic-research"
          element={<><p>自动研究过程</p><LocationProbe /></>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("EventCreatePage automatic research entry", () => {
  let adapter: MockResearchAdapter;

  beforeEach(() => {
    adapter = new MockResearchAdapter();
    setResearchClient(adapter);
  });

  afterEach(() => {
    resetResearchClient();
  });

  it("shows only one research input and one primary action", () => {
    renderCreatePage();

    expect(
      screen.getByRole("heading", { name: "告诉系统你想研究什么" }),
    ).toBeVisible();
    expect(screen.getByLabelText("研究主题或材料")).toBeVisible();
    expect(
      screen.getByText(/^写清楚关注的事件、公司或影响，或直接粘贴公告、研报或其他原始材料/),
    ).toBeVisible();
    expect(screen.getByLabelText("研究主题或材料")).toHaveAttribute(
      "placeholder",
      expect.stringMatching(/粘贴公告、研报或原始材料/),
    );
    expect(
      screen.getAllByRole("button", { name: "开始自动研究" }),
    ).toHaveLength(1);
    expect(screen.queryByText(/资料使用许可声明/)).not.toBeInTheDocument();
    expect(screen.queryByText(/关键因素/)).not.toBeInTheDocument();
    expect(screen.queryByText(/严格研究协议/)).not.toBeInTheDocument();
    expect(screen.queryByText(/决定材料归属/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/来源接入方式/)).not.toBeInTheDocument();
  });

  it("starts automatic research from the trimmed input and opens its process page", async () => {
    const user = userEvent.setup();
    const start = vi.spyOn(adapter, "startAutomaticResearch");
    renderCreatePage();

    await user.type(
      screen.getByLabelText("研究主题或材料"),
      "  英伟达新产品会如何影响供应链？  ",
    );
    await user.click(screen.getByRole("button", { name: "开始自动研究" }));

    await waitFor(() =>
      expect(start).toHaveBeenCalledWith("英伟达新产品会如何影响供应链？"),
    );
    expect(start).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("自动研究过程")).toBeVisible();
  });

  it("encodes the returned Case identity before opening the process route", async () => {
    const user = userEvent.setup();
    vi.spyOn(adapter, "startAutomaticResearch").mockResolvedValue({
      caseId: "case/with space?#",
      runId: "run-encoded",
      status: "queued",
    });
    renderCreatePage();

    await user.type(screen.getByLabelText("研究主题或材料"), "研究一个事件");
    await user.click(screen.getByRole("button", { name: "开始自动研究" }));

    expect(await screen.findByTestId("location")).toHaveTextContent(
      "/events/case%2Fwith%20space%3F%23/automatic-research",
    );
  });

  it("does not submit an empty input", async () => {
    const user = userEvent.setup();
    const start = vi.spyOn(adapter, "startAutomaticResearch");
    renderCreatePage();

    const submit = screen.getByRole("button", { name: "开始自动研究" });
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText("研究主题或材料"), "   ");
    expect(submit).toBeDisabled();
    expect(start).not.toHaveBeenCalled();
  });

  it("disables the form while the research is starting", async () => {
    const user = userEvent.setup();
    let resolveStart!: (value: {
      caseId: string;
      runId: string;
      status: "queued";
    }) => void;
    vi.spyOn(adapter, "startAutomaticResearch").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStart = resolve;
        }),
    );
    renderCreatePage();

    const input = screen.getByLabelText("研究主题或材料");
    await user.type(input, "研究一个事件");
    await user.click(screen.getByRole("button", { name: "开始自动研究" }));

    expect(screen.getByRole("button", { name: "正在启动…" })).toBeDisabled();
    expect(input).toBeDisabled();
    expect(screen.getByRole("form")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status")).toHaveTextContent("自动研究正在启动");
    resolveStart({ caseId: "case-loading", runId: "run-loading", status: "queued" });
    expect(await screen.findByText("自动研究过程")).toBeVisible();
  });

  it("does not navigate when a pending start resolves after leaving the page", async () => {
    const user = userEvent.setup();
    let resolveStart!: (value: {
      caseId: string;
      runId: string;
      status: "queued";
    }) => void;
    vi.spyOn(adapter, "startAutomaticResearch").mockImplementation(
      () => new Promise((resolve) => { resolveStart = resolve; }),
    );
    render(
      <MemoryRouter initialEntries={["/events/new"]}>
        <Routes>
          <Route path="/events/new" element={<LeaveableCreatePage />} />
          <Route path="/away" element={<><p>其他页面</p><LocationProbe /></>} />
          <Route path="/events/:caseId/automatic-research" element={<p>自动研究过程</p>} />
        </Routes>
      </MemoryRouter>,
    );

    await user.type(screen.getByLabelText("研究主题或材料"), "研究一个事件");
    await user.click(screen.getByRole("button", { name: "开始自动研究" }));
    expect(screen.getByRole("button", { name: "正在启动…" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "离开创建页" }));
    expect(await screen.findByText("其他页面")).toBeVisible();
    await act(async () => {
      resolveStart({ caseId: "late-case", runId: "late-run", status: "queued" });
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/away");
    expect(screen.queryByText("自动研究过程")).not.toBeInTheDocument();
  });

  it("announces a start failure inline and allows retry", async () => {
    const user = userEvent.setup();
    vi.spyOn(adapter, "startAutomaticResearch").mockRejectedValue(
      new Error("offline"),
    );
    renderCreatePage();

    await user.type(screen.getByLabelText("研究主题或材料"), "研究一个事件");
    await user.click(screen.getByRole("button", { name: "开始自动研究" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "自动研究未能启动，请稍后重试。",
    );
    expect(screen.getByRole("button", { name: "开始自动研究" })).toBeEnabled();
    expect(screen.getByLabelText("研究主题或材料")).toHaveAttribute(
      "aria-invalid",
      "false",
    );
    expect(screen.getByRole("alert")).toHaveFocus();
    expect(screen.getByLabelText("研究主题或材料")).not.toHaveFocus();
  });

  it("keeps secondary text and the textarea boundary above WCAG AA contrast", () => {
    const secondary = AUTOMATIC_RESEARCH_ENTRY_COLORS.secondary;
    const fieldBorder = AUTOMATIC_RESEARCH_ENTRY_COLORS.fieldBorder;
    const page = { lightness: .972, chroma: .008, hue: 85 };
    const field = { lightness: .985, chroma: .006, hue: 85 };

    expect(contrastRatio(secondary, page)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(secondary, field)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(fieldBorder, field)).toBeGreaterThanOrEqual(3);
  });
});

type Oklch = { lightness: number; chroma: number; hue: number };

function relativeLuminance({ lightness, chroma, hue }: Oklch): number {
  const radians = hue * Math.PI / 180;
  const a = chroma * Math.cos(radians);
  const b = chroma * Math.sin(radians);
  const l = (lightness + .3963377774 * a + .2158037573 * b) ** 3;
  const m = (lightness - .1055613458 * a - .0638541728 * b) ** 3;
  const s = (lightness - .0894841775 * a - 1.291485548 * b) ** 3;
  const red = Math.min(1, Math.max(0, 4.0767416621 * l - 3.3077115913 * m + .2309699292 * s));
  const green = Math.min(1, Math.max(0, -1.2684380046 * l + 2.6097574011 * m - .3413193965 * s));
  const blue = Math.min(1, Math.max(0, -.0041960863 * l - .7034186147 * m + 1.707614701 * s));
  return .2126 * red + .7152 * green + .0722 * blue;
}

function contrastRatio(first: Oklch, second: Oklch): number {
  const [lighter, darker] = [relativeLuminance(first), relativeLuminance(second)]
    .sort((left, right) => right - left);
  return (lighter + .05) / (darker + .05);
}
