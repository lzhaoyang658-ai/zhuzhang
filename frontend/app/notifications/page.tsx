"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  Bell,
  BellRing,
  Check,
  CheckCheck,
  ChevronRight,
  Clock3,
  Mail,
  Radar,
  RefreshCw,
  Settings2,
  ShieldAlert,
  X,
} from "lucide-react";
import { ApiError, api } from "@/lib/api";

type Level = "critical" | "warning" | "attention" | "info";
type Notice = {
  id: string;
  project_id: string;
  project_name: string;
  kind: "risk" | "event";
  code: string;
  level: Level;
  title: string;
  message: string;
  action_path: string;
  status: "active" | "resolved";
  read_at: string | null;
  resolved_at: string | null;
  first_triggered_at: string;
  last_triggered_at: string;
  occurrence_count: number;
};
type NoticeData = {
  summary: { active: number; unread: number; critical: number; resolved: number };
  items: Notice[];
};
type Preference = {
  email_enabled: boolean;
  email_digest_frequency: "off" | "daily" | "weekly";
  last_digest_at: string | null;
};
type Project = { id: string; name: string };

const levelCopy: Record<Level, { label: string; detail: string; icon: typeof Bell }> = {
  critical: { label: "严重", detail: "付款或继续增项前优先核对", icon: ShieldAlert },
  warning: { label: "警告", detail: "节点临近或预算接近边界", icon: AlertTriangle },
  attention: { label: "关注", detail: "预算已经偏离合同起点", icon: Radar },
  info: { label: "提示", detail: "仍有事项等待确认或跟进", icon: Clock3 },
};

function when(value: string) {
  const delta = Date.now() - new Date(value).getTime();
  const hours = Math.max(0, Math.floor(delta / 3_600_000));
  if (hours < 1) return "刚刚更新";
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  return days < 30
    ? `${days} 天前`
    : new Date(value).toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

export default function NotificationsPage() {
  const router = useRouter();
  const [data, setData] = useState<NoticeData | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [preference, setPreference] = useState<Preference | null>(null);
  const [filter, setFilter] = useState<"active" | "resolved" | "all">("active");
  const [project, setProject] = useState("all");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [draftEmailEnabled, setDraftEmailEnabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const settingsDialog = useRef<HTMLElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const bootstrapStarted = useRef(false);

  async function load() {
    const [notices, prefs, projectRows] = await Promise.all([
      api<NoticeData>("/notifications"),
      api<Preference>("/notification-preferences"),
      api<Project[]>("/projects"),
    ]);
    setData(notices);
    setPreference(prefs);
    setDraftEmailEnabled(prefs.email_enabled);
    setProjects(projectRows);
  }

  async function loadWorkspace() {
    setLoading(true);
    setError("");
    try {
      const auth = await api<{ authenticated: boolean }>("/auth/status");
      if (!auth.authenticated) {
        window.location.replace("/login");
        return;
      }
      await load();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        window.location.replace("/login");
        return;
      }
      setError(reason instanceof Error ? reason.message : "通知加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (bootstrapStarted.current) return;
    bootstrapStarted.current = true;
    // 首次挂载后启动鉴权与数据请求；重试仍复用同一加载入口。
    void loadWorkspace();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!settingsOpen) return;
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusableSelector = "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";
    const frame = window.requestAnimationFrame(() => {
      settingsDialog.current
        ?.querySelector<HTMLElement>("input:not([disabled]), select:not([disabled]), button:not([disabled])")
        ?.focus();
    });

    function handleDialogKeys(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setSettingsOpen(false);
        return;
      }
      if (event.key !== "Tab" || !settingsDialog.current) return;
      const focusable = Array.from(
        settingsDialog.current.querySelectorAll<HTMLElement>(focusableSelector),
      ).filter((item) => item.getClientRects().length > 0);
      if (!focusable.length) {
        event.preventDefault();
        settingsDialog.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!settingsDialog.current.contains(document.activeElement)) {
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
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", handleDialogKeys);
      document.body.style.overflow = previousOverflow;
      returnFocus.current?.focus();
    };
  }, [settingsOpen]);

  function openSettings() {
    setDraftEmailEnabled(preference?.email_enabled || false);
    setSettingsOpen(true);
  }

  async function openNotice(item: Notice) {
    setError("");
    if (!item.read_at) {
      try {
        await api(`/notifications/${item.id}/read`, { method: "POST" });
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "提醒已打开，但未能标记为已读");
      }
    }
    router.push(item.action_path);
  }

  async function readAll() {
    setBusy(true);
    setError("");
    try {
      await api("/notifications/actions/read-all", { method: "POST" });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function savePreference(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const enabled = form.get("email_enabled") === "on";
      const updated = await api<Preference>("/notification-preferences", {
        method: "PATCH",
        body: JSON.stringify({
          email_enabled: enabled,
          email_digest_frequency: enabled ? form.get("frequency") : "off",
        }),
      });
      setPreference(updated);
      setDraftEmailEnabled(updated.email_enabled);
      setSettingsOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "偏好保存失败");
    } finally {
      setBusy(false);
    }
  }

  if (loading && !data) {
    return (
      <main className="workspace-route workspace-notifications-v4 workspace-state-v4" aria-busy="true">
        <div className="loading-mark" aria-hidden="true">筑</div>
        <p>正在读取提醒…</p>
      </main>
    );
  }

  if (!data || !preference) {
    return (
      <main className="workspace-route workspace-notifications-v4 workspace-state-v4">
        <AlertTriangle aria-hidden="true" />
        <h1>提醒暂时没有加载出来</h1>
        <p>{error || "请检查网络连接后重新尝试。"}</p>
        <div className="workspace-state-v4-actions">
          <button type="button" onClick={() => void loadWorkspace()} disabled={loading}>
            <RefreshCw className={loading ? "spin" : ""} />
            {loading ? "正在重试" : "重新加载"}
          </button>
          <Link href="/projects">返回项目中心</Link>
        </div>
      </main>
    );
  }

  const visible = data.items.filter(
    (item) =>
      (filter === "all" || item.status === filter) &&
      (project === "all" || item.project_id === project),
  );
  const active = data.items.filter((item) => item.status === "active");
  const priority = active.find((item) => item.level === "critical") || active[0] || null;

  return (
    <main className="workspace-route workspace-notifications-v4">
      <nav className="workspace-v4-topbar" aria-label="通知中心导航">
        <Link className="workspace-v4-brand" href="/projects" aria-label="筑账项目中心">
          <span aria-hidden="true">筑</span>
          <strong>筑账</strong>
        </Link>
        <div className="workspace-v4-topbar-actions">
          <Link href="/projects"><ArrowLeft />项目中心</Link>
          <button type="button" onClick={openSettings}>
            <Settings2 />通知设置
          </button>
        </div>
      </nav>

      <header className="workspace-v4-page-head notifications-v4-head">
        <div>
          <p className="workspace-v4-eyebrow">风险与项目进度</p>
          <h1>提醒中心</h1>
          <p>
            {data.summary.active
              ? `${data.summary.active} 项仍需关注${data.summary.critical ? `，其中 ${data.summary.critical} 项为严重风险` : ""}。`
              : "当前没有待处理风险，新的预算和流程变化仍会继续检查。"}
          </p>
        </div>
        <button
          className="notifications-v4-read-all"
          type="button"
          onClick={() => void readAll()}
          disabled={busy || data.summary.unread === 0}
        >
          <CheckCheck />
          {data.summary.unread ? `全部标为已读 · ${data.summary.unread}` : "已全部读完"}
        </button>
      </header>

      <section className="notifications-v4-summary" aria-label="提醒摘要">
        <article>
          <span><BellRing />待处理</span>
          <strong>{data.summary.active}</strong>
          <small>仍在跟踪的风险与进度</small>
        </article>
        <article className={data.summary.critical ? "is-critical" : ""}>
          <span><ShieldAlert />严重风险</span>
          <strong>{data.summary.critical}</strong>
          <small>建议在付款或增项前处理</small>
        </article>
        <article>
          <span><Bell />未读</span>
          <strong>{data.summary.unread}</strong>
          <small>尚未查看的最新变化</small>
        </article>
        <article>
          <span><Check />已解除</span>
          <strong>{data.summary.resolved}</strong>
          <small>保留在历史记录中</small>
        </article>
      </section>

      <section className="notifications-v4-workbench">
        <div className="notifications-v4-main">
          <div className="notifications-v4-toolbar">
            <div>
              <p className="workspace-v4-eyebrow">通知流</p>
              <h2>需要你查看的事项</h2>
            </div>
            <div className="notifications-v4-controls">
              <div className="notifications-v4-filter" aria-label="按状态筛选">
                {(["active", "resolved", "all"] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    className={filter === value ? "is-active" : ""}
                    aria-pressed={filter === value}
                    onClick={() => setFilter(value)}
                  >
                    {value === "active" ? "当前" : value === "resolved" ? "历史" : "全部"}
                  </button>
                ))}
              </div>
              <label className="notifications-v4-project-filter">
                <span>项目</span>
                <select value={project} onChange={(event) => setProject(event.target.value)}>
                  <option value="all">全部项目</option>
                  {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </label>
            </div>
          </div>

          <div className="notifications-v4-list" aria-live="polite">
            {visible.length ? visible.map((item) => {
              const Icon = levelCopy[item.level].icon;
              const eventNotice = item.kind === "event";
              return (
                <article
                  className={`notifications-v4-item level-${item.level} ${item.read_at ? "is-read" : "is-unread"} ${item.status === "resolved" ? "is-resolved" : ""}`}
                  key={item.id}
                >
                  <button type="button" onClick={() => void openNotice(item)}>
                    <span className="notifications-v4-item-icon"><Icon /></span>
                    <span className="notifications-v4-item-body">
                      <span className="notifications-v4-item-meta">
                        <span>{eventNotice ? "项目进度" : levelCopy[item.level].label}</span>
                        <span>{item.project_name}</span>
                        <span>{when(item.last_triggered_at)}</span>
                      </span>
                      <strong>{item.title}</strong>
                      <span className="notifications-v4-item-copy">{item.message}</span>
                      <span className="notifications-v4-item-foot">
                        <span>
                          {item.status === "resolved"
                            ? `已于 ${when(item.resolved_at || item.last_triggered_at)}解除`
                            : item.read_at
                              ? "已读，仍在跟踪"
                              : "未读"}
                        </span>
                        {item.occurrence_count > 1 && <span>再次出现 {item.occurrence_count - 1} 次</span>}
                        <span className="notifications-v4-item-link">查看对象 <ChevronRight /></span>
                      </span>
                    </span>
                  </button>
                </article>
              );
            }) : (
              <div className="notifications-v4-empty">
                <Check />
                <h3>这个筛选下没有提醒</h3>
                <p>风险解除和已查看的进度都会保留在历史中。</p>
              </div>
            )}
          </div>
        </div>

        <aside className="notifications-v4-rail" aria-label="风险处理摘要">
          <section className="notifications-v4-priority">
            <p className="workspace-v4-eyebrow">当前优先级</p>
            {priority ? (
              <>
                <span className={`notifications-v4-priority-level level-${priority.level}`}>
                  {levelCopy[priority.level].label}
                </span>
                <h2>{priority.title}</h2>
                <p>{priority.message}</p>
                <button type="button" onClick={() => void openNotice(priority)}>
                  前往处理 <ChevronRight />
                </button>
              </>
            ) : (
              <>
                <Check />
                <h2>没有待处理风险</h2>
                <p>项目发生新的预算或流程变化时，这里会显示最高优先级事项。</p>
              </>
            )}
          </section>

          <section className="notifications-v4-levels">
            <header>
              <p className="workspace-v4-eyebrow">风险分布</p>
              <span>当前事项</span>
            </header>
            {(Object.keys(levelCopy) as Level[]).map((level) => {
              const item = levelCopy[level];
              const Icon = item.icon;
              return (
                <div key={level} className={`level-${level}`}>
                  <span><Icon />{item.label}</span>
                  <strong>{active.filter((notice) => notice.level === level).length}</strong>
                </div>
              );
            })}
          </section>

          <button className="notifications-v4-preference" type="button" onClick={openSettings}>
            <Mail />
            <span>
              <strong>{preference.email_enabled ? "邮件摘要已开启" : "邮件摘要未开启"}</strong>
              <small>
                {preference.email_enabled
                  ? preference.email_digest_frequency === "weekly" ? "每周汇总一次" : "每天汇总一次"
                  : "点击调整通知频率"}
              </small>
            </span>
            <ChevronRight />
          </button>
        </aside>
      </section>

      {settingsOpen && (
        <div
          className="notifications-v4-dialog-backdrop"
          onMouseDown={(event) => event.target === event.currentTarget && setSettingsOpen(false)}
        >
          <section
            className="notifications-v4-dialog"
            ref={settingsDialog}
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            aria-labelledby="notifications-v4-dialog-title"
          >
            <header>
              <div>
                <p className="workspace-v4-eyebrow">通知设置</p>
                <h2 id="notifications-v4-dialog-title">选择邮件摘要频率</h2>
              </div>
              <button type="button" onClick={() => setSettingsOpen(false)} aria-label="关闭通知设置"><X /></button>
            </header>
            <form onSubmit={savePreference}>
              <label className="notifications-v4-toggle">
                <input
                  name="email_enabled"
                  type="checkbox"
                  checked={draftEmailEnabled}
                  onChange={(event) => setDraftEmailEnabled(event.target.checked)}
                />
                <span>
                  <Mail />
                  <strong>接收邮件摘要</strong>
                  <small>严重预算风险和权限变化仍会保留站内通知。</small>
                </span>
              </label>
              <label className="notifications-v4-field">
                <span>摘要频率</span>
                <select
                  name="frequency"
                  defaultValue={preference.email_digest_frequency === "off" ? "daily" : preference.email_digest_frequency}
                  disabled={!draftEmailEnabled}
                >
                  <option value="daily">每天汇总</option>
                  <option value="weekly">每周汇总</option>
                </select>
              </label>
              <aside><Bell />生产邮件投递需要 SMTP 与独立定时调度器。</aside>
              <footer>
                <button type="button" onClick={() => setSettingsOpen(false)}>取消</button>
                <button disabled={busy}>{busy ? "正在保存" : "保存设置"}</button>
              </footer>
            </form>
          </section>
        </div>
      )}

      {error && (
        <div className="workspace-v4-error" role="alert">
          <AlertTriangle />
          <span>{error}</span>
          <button type="button" onClick={() => void loadWorkspace()} disabled={loading}>
            {loading ? "正在刷新" : "刷新状态"}
          </button>
          <button type="button" onClick={() => setError("")} aria-label="关闭错误提示"><X /></button>
        </div>
      )}
    </main>
  );
}
