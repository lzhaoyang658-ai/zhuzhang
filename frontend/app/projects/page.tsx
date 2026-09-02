"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  Bell,
  CalendarDays,
  Check,
  ChevronRight,
  FileArchive,
  Home,
  LockKeyhole,
  Plus,
  RefreshCw,
  WalletCards,
  X,
} from "lucide-react";
import { ApiError, api } from "@/lib/api";
import { Money } from "@/components/ui";

type ProjectSummary = {
  id: string;
  name: string;
  city: string;
  area_sqm: number;
  area_basis: string;
  renovation_type: string;
  status: string;
  fund_limit_cents: number;
  predicted_settlement_cents: number;
  paid_cents: number;
  role: "owner" | "co_manager" | "viewer";
  planned_end: string | null;
  deletion_scheduled_for: string | null;
  created_at: string;
};

type SessionInfo = { user: { id: string; name: string; email: string } };

const roles = {
  owner: "项目所有者",
  co_manager: "共同管理者",
  viewer: "只读成员",
};

const statusDetails: Record<string, { label: string; description: string }> = {
  施工中: { label: "施工中", description: "优先处理预算、变更和付款事项" },
  待结算: { label: "待结算", description: "核对预测结算与剩余付款" },
  准备中: { label: "准备中", description: "完善资金边界与开工计划" },
  已归档: { label: "已归档", description: "只读保留的历史装修记录" },
  待删除: { label: "待删除", description: "仍在七天撤销期内的项目" },
};

const activeStatusOrder = ["施工中", "待结算", "准备中"];
const wizardSteps = ["房屋信息", "计划时间", "资金边界"];

function formatDate(value: string | null) {
  if (!value) return "待设置";
  return new Date(value).toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function sortProjects(items: ProjectSummary[]) {
  return [...items].sort((left, right) => {
    if (left.planned_end && right.planned_end) {
      return left.planned_end.localeCompare(right.planned_end);
    }
    if (left.planned_end) return -1;
    if (right.planned_end) return 1;
    return right.created_at.localeCompare(left.created_at);
  });
}

export default function ProjectsPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [step, setStep] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [mobileNavigation, setMobileNavigation] = useState(false);
  const wizardForm = useRef<HTMLFormElement>(null);
  const wizardDialog = useRef<HTMLElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const pendingFocus = useRef<string | null>(null);
  const bootstrapStarted = useRef(false);

  const loadWorkspace = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const auth = await api<{ authenticated: boolean }>("/auth/status");
      if (!auth.authenticated) {
        window.location.replace("/login");
        return;
      }
      const [items, user] = await Promise.all([
        api<ProjectSummary[]>("/projects"),
        api<SessionInfo>("/session"),
      ]);
      setProjects(items);
      setSession(user);
      setWizardOpen(!items.length);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        window.location.replace("/login");
      } else {
        setLoadError(reason instanceof Error ? reason.message : "项目加载失败");
        setWizardOpen(false);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (bootstrapStarted.current) return;
    bootstrapStarted.current = true;
    // 首次挂载后启动鉴权与数据请求；重试复用同一加载入口。
    void loadWorkspace();
  }, [loadWorkspace]);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 640px)");
    const update = () => setMobileNavigation(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    if (!wizardOpen) return;
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusableSelector = "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

    function handleDialogKeys(event: KeyboardEvent) {
      if (event.key === "Escape" && projects.length > 0) {
        event.preventDefault();
        setWizardOpen(false);
        return;
      }
      if (event.key !== "Tab" || !wizardDialog.current) return;
      const focusable = Array.from(
        wizardDialog.current.querySelectorAll<HTMLElement>(focusableSelector),
      ).filter((item) => item.getClientRects().length > 0);
      if (!focusable.length) {
        event.preventDefault();
        wizardDialog.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!wizardDialog.current.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleDialogKeys);
    return () => {
      document.removeEventListener("keydown", handleDialogKeys);
      document.body.style.overflow = previousOverflow;
      returnFocus.current?.focus();
    };
  }, [wizardOpen, projects.length]);

  useEffect(() => {
    if (!wizardOpen) return;
    const frame = window.requestAnimationFrame(() => {
      const requested = pendingFocus.current;
      pendingFocus.current = null;
      const selector = requested
        ? `fieldset.active [name="${requested}"]`
        : "fieldset.active input, fieldset.active select, fieldset.active textarea";
      wizardDialog.current
        ?.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(selector)
        ?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [step, wizardOpen]);

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const form = new FormData(event.currentTarget);
    const plannedStart = String(form.get("planned_start") || "");
    const plannedEnd = String(form.get("planned_end") || "");
    const fundLimit = Number(form.get("fund_limit"));
    const reserve = Number(form.get("reserve") || 0);
    if (plannedStart && plannedEnd && plannedEnd < plannedStart) {
      pendingFocus.current = "planned_end";
      setStep(2);
      setError("计划完工日期不能早于开工日期");
      return;
    }
    if (reserve > fundLimit) {
      setStep(3);
      setError("风险预留金不能高于装修总资金上限");
      const reserveControl = event.currentTarget.elements.namedItem("reserve") as HTMLInputElement | null;
      reserveControl?.focus();
      return;
    }
    setBusy(true);
    try {
      const result = await api<{ id: string }>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: form.get("name"),
          city: form.get("city"),
          address: form.get("address") || null,
          area_sqm: Number(form.get("area_sqm")),
          area_basis: form.get("area_basis"),
          renovation_type: form.get("renovation_type"),
          planned_start: plannedStart,
          planned_end: plannedEnd,
          fund_limit_cents: Math.round(fundLimit * 100),
          reserve_cents: Math.round(reserve * 100),
          notes: form.get("notes"),
          status: "准备中",
        }),
      });
      router.push(`/?project=${result.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目创建失败");
      setStep(3);
    } finally {
      setBusy(false);
    }
  }

  function openWizard() {
    setStep(1);
    setError("");
    setWizardOpen(true);
  }

  function nextStep() {
    setError("");
    const fieldset = wizardForm.current?.querySelector("fieldset.active");
    const controls = Array.from(
      fieldset?.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(
        "input, select, textarea",
      ) || [],
    );
    const invalid = controls.find((control) => !control.checkValidity());
    if (invalid) {
      invalid.reportValidity();
      return;
    }
    if (step === 2) {
      const plannedStart = wizardForm.current?.elements.namedItem("planned_start") as HTMLInputElement | null;
      const plannedEnd = wizardForm.current?.elements.namedItem("planned_end") as HTMLInputElement | null;
      if (plannedStart?.value && plannedEnd?.value && plannedEnd.value < plannedStart.value) {
        setError("计划完工日期不能早于开工日期");
        plannedEnd.focus();
        return;
      }
    }
    setStep((value) => Math.min(3, value + 1));
  }

  const active = projects.filter((item) => !["已归档", "待删除"].includes(item.status));
  const archived = projects.filter((item) => item.status === "已归档");
  const pending = projects.filter((item) => item.status === "待删除");
  const totalFundLimit = projects.reduce((sum, item) => sum + item.fund_limit_cents, 0);
  const nextFinish = sortProjects(active.filter((item) => item.planned_end))[0] ?? null;
  const unknownActiveStatuses = Array.from(
    new Set(active.map((item) => item.status).filter((status) => !activeStatusOrder.includes(status))),
  );
  const statusGroups = [...activeStatusOrder, ...unknownActiveStatuses, "已归档", "待删除"]
    .map((status) => ({
      status,
      items: sortProjects(projects.filter((project) => project.status === status)),
      detail: statusDetails[status] ?? {
        label: status,
        description: "查看并继续处理这一状态下的项目",
      },
    }))
    .filter((group) => group.items.length > 0);

  if (loading) {
    return (
      <main className="loading-screen" aria-live="polite">
        <div className="loading-mark" aria-hidden="true">筑</div>
        <p>正在整理你的项目…</p>
      </main>
    );
  }

  if (loadError) {
    return (
      <main className="workspace-route workspace-projects-v4 workspace-state-v4">
        <LockKeyhole aria-hidden="true" />
        <h1>项目暂时没有加载出来</h1>
        <p>{loadError}</p>
        <div className="workspace-state-v4-actions">
          <button type="button" onClick={() => void loadWorkspace()} disabled={loading}>
            <RefreshCw className={loading ? "spin" : ""} aria-hidden="true" />
            {loading ? "正在重新加载" : "重新加载"}
          </button>
          <Link href="/login">返回登录页</Link>
        </div>
      </main>
    );
  }

  return (
    <main className="workspace-route workspace-projects-v4">
      <header className="workspace-projects__topbar">
        <Link className="workspace-projects__brand" href="/projects" aria-label="筑账项目中心">
          <span aria-hidden="true">筑</span>
          <strong>筑账</strong>
        </Link>
        <nav className="workspace-projects__nav" aria-label="全局导航">
          <Link href="/projects" aria-current="page">项目</Link>
          <Link href="/notifications">通知</Link>
          <Link href="/exports">导出记录</Link>
        </nav>
        {mobileNavigation && (
          <nav className="workspace-v4-topbar-actions" aria-label="手机快捷导航">
            <Link href="/notifications" aria-label="打开通知中心">
              <Bell aria-hidden="true" />
              通知
            </Link>
            <Link href="/exports" aria-label="打开导出记录">
              <FileArchive aria-hidden="true" />
              导出
            </Link>
          </nav>
        )}
        <div className="workspace-projects__account">
          <span>
            <strong>{session?.user.name}</strong>
            <small>{session?.user.email}</small>
          </span>
          <button type="button" onClick={openWizard}>
            <Plus aria-hidden="true" />
            创建项目
          </button>
        </div>
      </header>

      <div className="workspace-projects__page">
        <section className="workspace-projects__heading" aria-labelledby="projects-title">
          <div>
            <p>个人工作区</p>
            <h1 id="projects-title">装修项目</h1>
            <span>先找到正在处理的房子，再进入预算、变更与付款记录。</span>
          </div>
          <button type="button" onClick={openWizard}>
            <Plus aria-hidden="true" />
            创建装修项目
          </button>
        </section>

        <dl className="workspace-projects__summary" aria-label="项目总览">
          <div>
            <dt><Home aria-hidden="true" />全部项目</dt>
            <dd><span>{projects.length}</span><small>{active.length} 个仍在进行</small></dd>
          </div>
          <div>
            <dt><WalletCards aria-hidden="true" />资金边界合计</dt>
            <dd><span><Money cents={totalFundLimit} /></span><small>各项目独立核算</small></dd>
          </div>
          <div>
            <dt><CalendarDays aria-hidden="true" />最近计划完工</dt>
            <dd>
              <span>{nextFinish ? formatDate(nextFinish.planned_end) : "待设置"}</span>
              <small style={{ minWidth: 0, overflowWrap: "anywhere" }}>
                {nextFinish?.name ?? "暂无进行中的完工计划"}
              </small>
            </dd>
          </div>
          <div>
            <dt><LockKeyhole aria-hidden="true" />生命周期</dt>
            <dd><span>{archived.length} / {pending.length}</span><small>已归档 / 待删除</small></dd>
          </div>
        </dl>

        {error && !wizardOpen && (
          <div className="workspace-projects__error" role="alert">
            <LockKeyhole aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        <section className="workspace-projects__groups" aria-labelledby="project-list-title">
          <header className="workspace-projects__list-heading">
            <div>
              <p>项目列表</p>
              <h2 id="project-list-title">按当前状态继续工作</h2>
            </div>
            <span>{active.length ? `优先显示 ${active.length} 个进行中项目` : "当前没有进行中的项目"}</span>
          </header>

          {statusGroups.length > 0 ? statusGroups.map((group) => (
            <section
              className="workspace-projects__group"
              data-status={group.status}
              key={group.status}
              aria-labelledby={`project-status-${group.status}`}
            >
              <header>
                <div>
                  <h3 id={`project-status-${group.status}`}>{group.detail.label}</h3>
                  <p>{group.detail.description}</p>
                </div>
                <span>{group.items.length} 个项目</span>
              </header>
              <div className="workspace-projects__rows">
                {group.items.map((project) => (
                  <article className="workspace-projects__project-row" key={project.id}>
                    <div className="workspace-projects__project-primary" style={{ minWidth: 0 }}>
                      <span className="workspace-projects__status">{group.detail.label}</span>
                      <h4 style={{ minWidth: 0, overflowWrap: "anywhere" }}>
                        <Link href={`/?project=${project.id}`} style={{ overflowWrap: "anywhere" }}>
                          {project.name}
                        </Link>
                      </h4>
                      <p style={{ overflowWrap: "anywhere" }}>
                        {project.city} · {project.area_sqm}㎡ {project.area_basis} · {project.renovation_type}
                      </p>
                      <small>{roles[project.role]}</small>
                    </div>
                    <dl className="workspace-projects__project-finance">
                      <div>
                        <dt>预测结算</dt>
                        <dd><Money cents={project.predicted_settlement_cents} /></dd>
                      </div>
                      <div>
                        <dt>已付款</dt>
                        <dd><Money cents={project.paid_cents} /></dd>
                      </div>
                      <div>
                        <dt>资金上限</dt>
                        <dd><Money cents={project.fund_limit_cents} /></dd>
                      </div>
                    </dl>
                    <div className="workspace-projects__project-schedule">
                      <span>{project.status === "待删除" ? "计划删除" : "计划完工"}</span>
                      <time dateTime={project.deletion_scheduled_for ?? project.planned_end ?? undefined}>
                        {formatDate(project.status === "待删除" ? project.deletion_scheduled_for : project.planned_end)}
                      </time>
                    </div>
                    <Link
                      className="workspace-projects__project-action"
                      href={`/?project=${project.id}`}
                      aria-label={`进入项目：${project.name}`}
                    >
                      进入项目
                      <ChevronRight aria-hidden="true" />
                    </Link>
                  </article>
                ))}
              </div>
            </section>
          )) : (
            <section className="workspace-projects__empty">
              <div aria-hidden="true"><Home /></div>
              <p>还没有装修项目</p>
              <h2>先建立第一套房的资金边界</h2>
              <span>创建后会自动生成预算类别和默认付款节点。</span>
              <button type="button" onClick={openWizard}>
                创建第一个项目
                <ArrowRight aria-hidden="true" />
              </button>
            </section>
          )}
        </section>
      </div>

      {wizardOpen && (
        <div
          className="wizard-backdrop project-wizard-v4__backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && projects.length > 0) setWizardOpen(false);
          }}
        >
          <section
            className="project-wizard project-wizard-v4"
            ref={wizardDialog}
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            aria-labelledby="wizard-title"
            aria-describedby="wizard-description"
          >
            <header className="project-wizard-v4__header">
              <div>
                <p>创建装修项目</p>
                <h2 id="wizard-title">确定这套房的基础边界</h2>
                <span id="wizard-description">三步完成，创建后仍可在项目设置中调整。</span>
              </div>
              {projects.length > 0 && (
                <button type="button" onClick={() => setWizardOpen(false)} aria-label="关闭创建向导">
                  <X aria-hidden="true" />
                </button>
              )}
            </header>

            <div
              className="wizard-progress project-wizard-v4__progress"
              role="progressbar"
              aria-label="创建项目进度"
              aria-valuemin={1}
              aria-valuemax={3}
              aria-valuenow={step}
            >
              <i style={{ width: `${step / 3 * 100}%` }} />
              <span aria-live="polite">第 {step} 步，共 3 步</span>
            </div>
            <ol className="project-wizard-v4__steps" aria-label="创建步骤">
              {wizardSteps.map((label, index) => {
                const number = index + 1;
                return (
                  <li
                    className={number === step ? "active" : number < step ? "complete" : ""}
                    aria-current={number === step ? "step" : undefined}
                    key={label}
                  >
                    <span>{number < step ? <Check aria-hidden="true" /> : number}</span>
                    {label}
                  </li>
                );
              })}
            </ol>

            <form ref={wizardForm} onSubmit={createProject}>
              <fieldset className={step === 1 ? "active" : ""} hidden={step !== 1}>
                <legend>房子与装修方式</legend>
                <label>
                  <span>项目名称</span>
                  <input name="name" required maxLength={120} placeholder="例如：梧桐路新家" />
                </label>
                <div className="wizard-grid">
                  <label>
                    <span>所在城市</span>
                    <input name="city" required maxLength={60} placeholder="杭州" />
                  </label>
                  <label>
                    <span>小区或地址（选填）</span>
                    <input name="address" maxLength={240} placeholder="不必填写完整门牌" />
                  </label>
                </div>
                <div className="wizard-grid three">
                  <label>
                    <span>面积</span>
                    <input name="area_sqm" type="number" min="1" max="10000" required />
                  </label>
                  <label>
                    <span>面积口径</span>
                    <select name="area_basis">
                      <option>套内面积</option>
                      <option>建筑面积</option>
                    </select>
                  </label>
                  <label>
                    <span>装修方式</span>
                    <select name="renovation_type">
                      <option>清包</option>
                      <option>半包</option>
                      <option>全包</option>
                      <option>整装</option>
                    </select>
                  </label>
                </div>
              </fieldset>

              <fieldset className={step === 2 ? "active" : ""} hidden={step !== 2}>
                <legend>计划时间</legend>
                <p>日期可选，用于生成默认付款节点；未填写时以创建日为起点。</p>
                <div className="wizard-grid">
                  <label>
                    <span>计划开工</span>
                    <input name="planned_start" type="date" />
                  </label>
                  <label>
                    <span>计划完工</span>
                    <input name="planned_end" type="date" />
                  </label>
                </div>
                <aside>
                  <CalendarDays aria-hidden="true" />
                  <div>
                    <strong>自动生成 5 个付款节点</strong>
                    <span>开工、水电、泥木、竣工和尾款按资金上限与计划开工日生成，节点创建后暂不可编辑。</span>
                  </div>
                </aside>
              </fieldset>

              <fieldset className={step === 3 ? "active" : ""} hidden={step !== 3}>
                <legend>资金边界</legend>
                <div className="wizard-grid">
                  <label>
                    <span>装修总资金上限（元）</span>
                    <input name="fund_limit" type="number" min="1" step="1" required placeholder="450000" />
                  </label>
                  <label>
                    <span>风险预留金（元）</span>
                    <input name="reserve" type="number" min="0" step="1" defaultValue="0" />
                  </label>
                </div>
                <label>
                  <span>备注（选填）</span>
                  <textarea name="notes" maxLength={2000} rows={3} placeholder="记录这套房最重要的预算原则" />
                </label>
                <aside>
                  <Check aria-hidden="true" />
                  <div>
                    <strong>同时生成 12 个默认预算类别</strong>
                    <span>从拆改、水电到家具家电，不需要从空白表格开始。</span>
                  </div>
                </aside>
              </fieldset>

              {error && <p className="wizard-error" role="alert">{error}</p>}

              <footer>
                <button
                  type="button"
                  disabled={step === 1}
                  onClick={() => {
                    setError("");
                    setStep((value) => Math.max(1, value - 1));
                  }}
                >
                  上一步
                </button>
                {step < 3 ? (
                  <button type="button" className="wizard-next" onClick={nextStep}>
                    继续
                    <ArrowRight aria-hidden="true" />
                  </button>
                ) : (
                  <button className="wizard-next" disabled={busy}>
                    {busy ? "正在创建" : "创建并进入项目"}
                    <ArrowRight aria-hidden="true" />
                  </button>
                )}
              </footer>
            </form>
          </section>
        </div>
      )}
    </main>
  );
}
