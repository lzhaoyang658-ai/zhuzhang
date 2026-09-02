import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "@/app/page";

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
}));

vi.mock("@/lib/api", () => ({
  API_BASE: "https://api.example.test/api/v1",
  ApiError: class ApiError extends Error {
    status = 500;
  },
  api: mocks.api,
  authHeaders: () => ({}),
}));

vi.mock("@gsap/react", () => ({ useGSAP: () => undefined }));
vi.mock("gsap", () => ({
  default: {
    registerPlugin: vi.fn(),
    set: vi.fn(),
    fromTo: vi.fn(),
    utils: { toArray: () => [] },
  },
}));
vi.mock("gsap/ScrollTrigger", () => ({ ScrollTrigger: { create: vi.fn() } }));

const dashboard = {
  project: {
    id: "project-1",
    name: "面试演示项目",
    city: "上海",
    area_sqm: 88,
    area_basis: "套内",
    renovation_type: "全屋装修",
    status: "进行中",
    planned_end: "2026-12-31",
  },
  budget: {
    fund_limit_cents: 500_000_00,
    baseline_cents: 300_000_00,
    baseline_version: 1,
    approved_change_cents: 0,
    approved_budget_cents: 300_000_00,
    pending_risk_cents: 0,
    predicted_settlement_cents: 300_000_00,
    paid_cents: 0,
    remaining_funds_cents: 500_000_00,
    next_30_days_cents: 0,
    approved_overrun_rate: 0,
    predicted_overrun_rate: 0,
    payment_progress: 0,
  },
  alerts: [],
  changes: [],
  next_milestone: null,
  timeline: [],
};

type SessionOptions = {
  role?: "owner" | "co_manager" | "viewer";
  uploadsEnabled?: boolean;
  omitCapabilities?: boolean;
};

function mockWorkspace({ role = "owner", uploadsEnabled = false, omitCapabilities = false }: SessionOptions = {}) {
  mocks.api.mockImplementation(async (path: string) => {
    if (path === "/auth/status") return { authenticated: true };
    if (path === "/projects") return [{ id: "project-1" }];
    if (path === "/session") return {
      user: { id: "user-1", name: "林然", email: "lin@example.com" },
      memberships: [{ project_id: "project-1", role }],
      mode: "production",
      ...(omitCapabilities ? {} : { capabilities: { uploads_enabled: uploadsEnabled } }),
    };
    if (path === "/projects/project-1/dashboard") return dashboard;
    if (path === "/projects/project-1/milestones") return [];
    if (path === "/projects/project-1/changes") return [];
    if (path === "/projects/project-1/timeline") return [];
    if (path === "/projects/project-1/evidence") return [];
    if (path === "/projects/project-1/payments") return [];
    if (path === "/projects/project-1/quotes") return [];
    if (path === "/notifications/unread-count") return { unread: 0 };
    throw new Error(`Unexpected API path: ${path}`);
  });
}

async function renderWorkspace(options?: SessionOptions) {
  mockWorkspace(options);
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  window.history.replaceState({}, "", "/");
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  const view = render(<HomePage />);
  await screen.findByRole("heading", { name: "下一笔钱，先过付款检查。" });
  return { ...view, fetchMock };
}

function openWorkspaceTab(name: string) {
  fireEvent.click(within(screen.getByRole("navigation", { name: "项目导航" })).getByRole("button", { name }));
}

describe("workspace upload capability", () => {
  beforeEach(() => {
    mocks.api.mockReset();
    mocks.push.mockReset();
  });

  it("disables every upload entry and guards quote drops in demo mode", async () => {
    const { container, fetchMock } = await renderWorkspace({ role: "owner", uploadsEnabled: false });

    expect(screen.getByText("演示环境已关闭文件上传")).toBeInTheDocument();
    expect(screen.getByText(/不代表账号被设为只读/)).toBeInTheDocument();
    const overviewUpload = screen.getByRole("button", { name: /上传证据/ });
    expect(overviewUpload).toBeDisabled();
    expect(overviewUpload).toHaveAttribute("title", "演示环境已关闭文件上传；完整部署可启用。");
    fireEvent.click(overviewUpload);
    expect(screen.queryByRole("dialog", { name: "上传项目证据" })).not.toBeInTheDocument();

    openWorkspaceTab("报价与预算");
    await screen.findByRole("heading", { name: "演示环境已关闭候选报价上传" });
    const quoteInput = screen.getByLabelText("导入候选报价文件");
    expect(quoteInput).toBeDisabled();
    const quoteDropzone = container.querySelector(".quote-upload");
    expect(quoteDropzone).not.toBeNull();
    fireEvent.drop(quoteDropzone!, { dataTransfer: { files: [new File(["name,total"], "quote.csv", { type: "text/csv" })] } });
    await screen.findByText("演示环境已关闭文件上传；完整部署可启用。");
    expect(fetchMock).not.toHaveBeenCalled();

    openWorkspaceTab("证据与时间线");
    await screen.findByRole("heading", { name: "事实按发生顺序保留" });
    expect(screen.getAllByText("演示环境不接收新附件").length).toBeGreaterThan(0);
    for (const button of screen.getAllByRole("button", { name: "上传证据" })) expect(button).toBeDisabled();
    expect(screen.queryByRole("dialog", { name: "上传项目证据" })).not.toBeInTheDocument();
  });

  it("keeps account read-only messaging separate when uploads are enabled", async () => {
    await renderWorkspace({ role: "viewer", uploadsEnabled: true });

    expect(screen.queryByText("演示环境已关闭文件上传")).not.toBeInTheDocument();
    expect(screen.getByText("只读浏览")).toBeInTheDocument();
    openWorkspaceTab("报价与预算");
    await screen.findByRole("heading", { name: "只读浏览候选报价" });
    expect(screen.getByText(/当前账号可以查看、选择和对比报价/)).toBeInTheDocument();
    expect(screen.getByLabelText("导入候选报价文件")).toBeDisabled();
  });

  it("fails safe while an older session response has no capabilities", async () => {
    await renderWorkspace({ role: "owner", omitCapabilities: true });

    expect(screen.getByText("当前环境尚未开放文件上传能力")).toBeInTheDocument();
    const overviewUpload = screen.getByRole("button", { name: /上传证据/ });
    expect(overviewUpload).toBeDisabled();
    expect(overviewUpload).toHaveAttribute("title", "当前环境尚未开放文件上传能力；完整部署可启用。");
    openWorkspaceTab("报价与预算");
    await screen.findByRole("heading", { name: "当前环境尚未开放候选报价上传" });
    expect(screen.getByLabelText("导入候选报价文件")).toBeDisabled();
  });

  it("keeps evidence upload available for writable projects when capability is enabled", async () => {
    await renderWorkspace({ role: "owner", uploadsEnabled: true });

    const overviewUpload = screen.getByRole("button", { name: /上传证据/ });
    expect(overviewUpload).toBeEnabled();
    fireEvent.click(overviewUpload);
    expect(await screen.findByRole("dialog", { name: "上传项目证据" })).toBeInTheDocument();
  });
});
