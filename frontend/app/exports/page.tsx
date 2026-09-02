"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Archive,
  ArrowLeft,
  Check,
  Clock3,
  Download,
  FileArchive,
  FolderLock,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { API_BASE, ApiError, api } from "@/lib/api";

type Project = {
  id: string;
  name: string;
  city: string;
  status: string;
  role: "owner" | "co_manager" | "viewer";
};
type ExportArtifact = {
  id: string;
  kind: "primary" | "attachments";
  part_number: number;
  filename: string;
  size_bytes: number;
  integrity_protected: boolean;
  storage_backend: string;
  download_path: string;
};
type ExportJob = {
  id: string;
  project_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "dead_letter" | "expired";
  progress: number;
  stage: string;
  include_attachments: boolean;
  date_from: string | null;
  date_to: string | null;
  file_size_bytes: number | null;
  integrity_protected: boolean;
  storage_backend: string;
  report_version: string;
  report_page_count: number | null;
  part_count: number;
  attempt_count: number;
  max_attempts: number;
  artifacts: ExportArtifact[];
  error_message: string | null;
  expires_at: string | null;
  downloadable: boolean;
  created_at: string;
  finished_at: string | null;
};

const statusCopy: Record<ExportJob["status"], string> = {
  queued: "等待生成",
  running: "正在整理",
  succeeded: "可以下载",
  failed: "生成失败",
  dead_letter: "需要人工重试",
  expired: "链接已过期",
};

function formatBytes(value: number | null) {
  if (!value) return "尚未生成";
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(value: string | null) {
  if (!value) return "等待任务完成";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export default function ExportsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [jobs, setJobs] = useState<ExportJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [permissionMissing, setPermissionMissing] = useState(false);
  const [error, setError] = useState("");
  const [jobsError, setJobsError] = useState("");
  const bootstrapStarted = useRef(false);

  async function loadJobs(id: string) {
    const nextJobs = await api<ExportJob[]>(`/projects/${id}/export-jobs`);
    setJobs(nextJobs);
    setJobsError("");
    return nextJobs;
  }

  async function loadWorkspace() {
    setLoading(true);
    setError("");
    setJobsError("");
    setPermissionMissing(false);
    try {
      const auth = await api<{ authenticated: boolean }>("/auth/status");
      if (!auth.authenticated) {
        window.location.replace("/login");
        return;
      }
      const allProjects = await api<Project[]>("/projects");
      const owned = allProjects.filter((item) => item.role === "owner");
      setProjects(owned);
      if (!owned.length) {
        setProjectId("");
        setJobs([]);
        setPermissionMissing(true);
        return;
      }
      const requested = new URLSearchParams(window.location.search).get("project");
      const selected = owned.find((item) => item.id === requested) || owned[0];
      setProjectId(selected.id);
      try {
        await loadJobs(selected.id);
      } catch (reason) {
        setJobs([]);
        setJobsError(reason instanceof Error ? reason.message : "任务状态加载失败");
      }
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        window.location.replace("/login");
        return;
      }
      setError(reason instanceof Error ? reason.message : "档案工作台加载失败");
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

  const hasActiveJob = jobs.some((item) => item.status === "queued" || item.status === "running");

  useEffect(() => {
    if (!projectId || !hasActiveJob) return;
    const timer = window.setInterval(() => {
      loadJobs(projectId).catch((reason) => {
        setJobsError(reason instanceof Error ? reason.message : "进度刷新失败");
      });
    }, 2200);
    return () => window.clearInterval(timer);
  }, [projectId, hasActiveJob]);

  async function switchProject(id: string) {
    if (!id || id === projectId) return;
    setProjectId(id);
    setJobs([]);
    setError("");
    setJobsError("");
    setJobsLoading(true);
    window.history.replaceState(null, "", `/exports?project=${id}`);
    try {
      await loadJobs(id);
    } catch (reason) {
      setJobsError(reason instanceof Error ? reason.message : "任务加载失败");
    } finally {
      setJobsLoading(false);
    }
  }

  async function refreshJobs() {
    if (!projectId) return;
    setJobsLoading(true);
    setError("");
    setJobsError("");
    try {
      await loadJobs(projectId);
    } catch (reason) {
      setJobsError(reason instanceof Error ? reason.message : "任务刷新失败");
    } finally {
      setJobsLoading(false);
    }
  }

  async function createExport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!projectId) {
      setError("请先选择一个你拥有的项目");
      return;
    }
    if (jobsError) {
      setError("请先刷新任务状态，再创建新档案");
      return;
    }
    const form = new FormData(event.currentTarget);
    const dateFrom = String(form.get("date_from") || "");
    const dateTo = String(form.get("date_to") || "");
    if (dateFrom && dateTo && dateFrom > dateTo) {
      setError("开始日期不能晚于结束日期");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api<ExportJob>(`/projects/${projectId}/export-jobs`, {
        method: "POST",
        body: JSON.stringify({
          include_attachments: form.get("include_attachments") === "on",
          date_from: dateFrom || null,
          date_to: dateTo || null,
        }),
      });
      try {
        await loadJobs(projectId);
      } catch (reason) {
        setJobsError(reason instanceof Error ? reason.message : "任务已创建，但最新状态加载失败");
      }
      document.querySelector(".exports-v4-queue")?.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
        block: "start",
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务创建失败");
    } finally {
      setBusy(false);
    }
  }

  async function retry(job: ExportJob) {
    setBusy(true);
    setError("");
    try {
      await api(`/project-export-jobs/${job.id}/retry`, { method: "POST" });
      try {
        await loadJobs(projectId);
      } catch (reason) {
        setJobsError(reason instanceof Error ? reason.message : "已提交重新生成，但状态刷新失败");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重试失败");
    } finally {
      setBusy(false);
    }
  }

  async function download(job: ExportJob, artifact?: ExportArtifact) {
    setBusy(true);
    setError("");
    try {
      const path = artifact?.download_path || `/project-export-jobs/${job.id}/download`;
      const response = await fetch(`${API_BASE}${path}`, { credentials: "include" });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error?.message || "档案下载失败");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = artifact?.filename || "项目档案-主卷.zip";
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "档案下载失败");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <main className="workspace-route workspace-exports-v4 workspace-state-v4" aria-busy="true">
        <div className="loading-mark" aria-hidden="true">筑</div>
        <p>正在打开档案工作台…</p>
      </main>
    );
  }

  if (permissionMissing) {
    return (
      <main className="workspace-route workspace-exports-v4 workspace-state-v4">
        <FolderLock aria-hidden="true" />
        <p className="workspace-v4-eyebrow">项目档案</p>
        <h1>当前没有可导出的项目</h1>
        <p>完整档案包含原始证据和付款记录，只有项目所有者可以生成与下载。共同管理员和查看者可以继续在项目内协作。</p>
        <div className="workspace-state-v4-actions">
          <Link href="/projects"><ArrowLeft />返回项目中心</Link>
        </div>
      </main>
    );
  }

  if (!projectId) {
    return (
      <main className="workspace-route workspace-exports-v4 workspace-state-v4">
        <AlertTriangle aria-hidden="true" />
        <h1>档案工作台暂时没有加载出来</h1>
        <p>{error || "请检查网络连接后重新尝试。"}</p>
        <div className="workspace-state-v4-actions">
          <button type="button" onClick={() => void loadWorkspace()}>
            <RefreshCw />重新加载
          </button>
          <Link href="/projects">返回项目中心</Link>
        </div>
      </main>
    );
  }

  const currentProject = projects.find((item) => item.id === projectId);
  const completed = jobs.filter((item) => item.status === "succeeded" && item.downloadable).length;
  const activeCount = jobs.filter((item) => item.status === "queued" || item.status === "running").length;

  return (
    <main className="workspace-route workspace-exports-v4">
      <nav className="workspace-v4-topbar" aria-label="项目档案导航">
        <Link className="workspace-v4-brand" href="/projects" aria-label="筑账项目中心">
          <span aria-hidden="true">筑</span>
          <strong>筑账</strong>
        </Link>
        <div className="workspace-v4-topbar-actions">
          <Link href={`/?project=${projectId}`}><ArrowLeft />返回项目</Link>
        </div>
      </nav>

      <header className="workspace-v4-page-head exports-v4-head">
        <div>
          <p className="workspace-v4-eyebrow">备份与交接</p>
          <h1>项目档案</h1>
          <p>按范围生成正式报告、结构化记录与原始附件。任务会在后台继续，完成后可在这里下载。</p>
        </div>
        <label className="exports-v4-project-select">
          <span>当前项目</span>
          <select value={projectId} onChange={(event) => void switchProject(event.target.value)} disabled={jobsLoading}>
            {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
      </header>

      <section className="exports-v4-summary" aria-label="档案任务摘要">
        <article>
          <span>历史任务</span>
          <strong>{jobsError ? "—" : jobs.length}</strong>
          <small>{currentProject?.name || "当前项目"}</small>
        </article>
        <article>
          <span>生成中</span>
          <strong>{jobsError ? "—" : activeCount}</strong>
          <small>页面关闭不会取消</small>
        </article>
        <article>
          <span>可下载</span>
          <strong>{jobsError ? "—" : completed}</strong>
          <small>链接保留 24 小时</small>
        </article>
        <article>
          <span><ShieldCheck />权限校验</span>
          <strong>所有者</strong>
          <small>下载前再次校验身份</small>
        </article>
      </section>

      <section className="exports-v4-workbench">
        <aside className="exports-v4-create-panel" aria-labelledby="exports-v4-create-title">
          <header>
            <p className="workspace-v4-eyebrow">新建任务</p>
            <h2 id="exports-v4-create-title">创建项目档案</h2>
            <p>项目汇总始终反映当前完整状态；日期范围只影响过程记录和附件。</p>
          </header>
          <form onSubmit={createExport}>
            <label className="exports-v4-check">
              <input type="checkbox" name="include_attachments" defaultChecked />
              <span>
                <Check />
                <strong>包含证据与原始报价</strong>
                <small>附件较多时，生成时间和文件体积会增加。</small>
              </span>
            </label>
            <div className="exports-v4-date-range">
              <label>
                <span>开始日期</span>
                <input type="date" name="date_from" />
              </label>
              <label>
                <span>结束日期</span>
                <input type="date" name="date_to" />
              </label>
            </div>
            <button type="submit" disabled={busy || jobsLoading || !!jobsError || hasActiveJob}>
              {jobsError ? (
                <><AlertTriangle />任务状态未加载</>
              ) : jobsLoading ? (
                <><LoaderCircle className="spin" />正在核对任务</>
              ) : hasActiveJob ? (
                <><LoaderCircle className="spin" />已有任务正在生成</>
              ) : busy ? (
                <><LoaderCircle className="spin" />正在创建</>
              ) : (
                <><Archive />生成项目档案</>
              )}
            </button>
            <small><Clock3 />生成完成后会在站内通知中心提醒你。</small>
          </form>
          <div className="exports-v4-create-note">
            <FileArchive />
            <p><strong>档案包含什么？</strong>正式 PDF、预算与付款 CSV，以及按需打包的证据和原始报价。</p>
          </div>
        </aside>

        <section className="exports-v4-queue" aria-labelledby="exports-v4-queue-title" aria-busy={jobsLoading}>
          <header className="exports-v4-queue-head">
            <div>
              <p className="workspace-v4-eyebrow">后台任务</p>
              <h2 id="exports-v4-queue-title">任务队列</h2>
              <p>每次生成都有独立进度、完整性校验和下载有效期。</p>
            </div>
            <button type="button" onClick={() => void refreshJobs()} disabled={jobsLoading}>
              <RefreshCw className={jobsLoading ? "spin" : ""} />
              {jobsLoading ? "刷新中" : "刷新"}
            </button>
          </header>

          <div className="exports-v4-job-list" aria-live="polite">
            {jobsError ? (
              <div className="exports-v4-empty" role="alert">
                <AlertTriangle />
                <h3>任务状态没有加载出来</h3>
                <p>{jobsError}</p>
                <button
                  className="exports-v4-retry"
                  type="button"
                  onClick={() => void refreshJobs()}
                  disabled={jobsLoading}
                >
                  <RefreshCw className={jobsLoading ? "spin" : ""} />
                  {jobsLoading ? "正在重新加载" : "重新加载任务"}
                </button>
              </div>
            ) : jobsLoading && !jobs.length ? (
              <div className="exports-v4-empty"><LoaderCircle className="spin" /><p>正在读取任务…</p></div>
            ) : jobs.length ? jobs.map((job, index) => {
              const progress = Math.max(0, Math.min(100, job.progress));
              const statusLabel = job.status === "succeeded" && !job.downloadable
                ? "下载已失效"
                : statusCopy[job.status];
              const canRetry = !job.downloadable && (
                job.status === "failed" ||
                job.status === "dead_letter" ||
                job.status === "expired" ||
                job.status === "succeeded"
              );
              return (
                <article className={`exports-v4-job status-${job.status}`} key={job.id}>
                  <header>
                    <div>
                      <span className="exports-v4-job-status">{statusLabel}</span>
                      <small>{formatTime(job.created_at)}</small>
                    </div>
                    <span className="exports-v4-job-index">{String(index + 1).padStart(2, "0")}</span>
                  </header>

                  <div className="exports-v4-job-title">
                    <div>
                      <h3>{job.include_attachments ? "完整正式档案" : "轻量正式档案"}</h3>
                      <p>{job.date_from || job.date_to ? `${job.date_from || "项目开始"} 至 ${job.date_to || "生成时"}` : "项目全部时间范围"}</p>
                    </div>
                    <span>{formatBytes(job.file_size_bytes)}</span>
                  </div>

                  <div
                    className="exports-v4-progress"
                    role="progressbar"
                    aria-label={`${statusLabel}，${progress}%`}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={progress}
                  >
                    <i style={{ width: `${progress}%` }} />
                  </div>
                  <div className="exports-v4-job-stage">
                    <span>{job.stage}</span>
                    <strong>{progress}%</strong>
                  </div>

                  {(job.report_page_count || job.report_version || job.integrity_protected) && (
                    <div className="exports-v4-job-facts">
                      {job.report_page_count && <span>{job.report_page_count} 页报告</span>}
                      <span>{job.part_count || 1} 个分卷</span>
                      {job.report_version && <span>{job.report_version}</span>}
                      <span>{job.integrity_protected ? "SHA-256 已校验" : "等待完整性校验"}</span>
                    </div>
                  )}

                  {job.downloadable && (
                    <div className="exports-v4-downloads" aria-label="可下载档案分卷">
                      {job.artifacts.length ? job.artifacts.map((artifact) => (
                        <button
                          type="button"
                          key={artifact.id}
                          onClick={() => void download(job, artifact)}
                          disabled={busy}
                          aria-label={`下载 ${artifact.filename}`}
                        >
                          <span>
                            <Download />
                            <strong>{artifact.kind === "primary" ? "主卷 · 正式报告" : `附件卷 ${String(artifact.part_number).padStart(2, "0")}`}</strong>
                          </span>
                          <small>{formatBytes(artifact.size_bytes)}</small>
                        </button>
                      )) : (
                        <button type="button" onClick={() => void download(job)} disabled={busy}>
                          <span><Download /><strong>下载项目档案</strong></span>
                          <small>{formatBytes(job.file_size_bytes)}</small>
                        </button>
                      )}
                    </div>
                  )}

                  {canRetry && (
                    <button className="exports-v4-retry" type="button" onClick={() => void retry(job)} disabled={busy}>
                      <RefreshCw />重新生成
                    </button>
                  )}
                  {job.error_message && <aside className="exports-v4-job-error"><AlertTriangle />{job.error_message}</aside>}
                  <footer>
                    {job.attempt_count > 0 && job.status !== "succeeded" && (
                      <small>已执行 {job.attempt_count} / {job.max_attempts} 次</small>
                    )}
                    {job.expires_at && job.downloadable && <small>下载有效至 {formatTime(job.expires_at)}</small>}
                  </footer>
                </article>
              );
            }) : (
              <div className="exports-v4-empty">
                <Archive />
                <h3>还没有生成记录</h3>
                <p>在左侧创建第一份档案后，进度、有效期和下载入口会出现在这里。</p>
              </div>
            )}
          </div>
        </section>
      </section>

      {error && (
        <div className="workspace-v4-error" role="alert">
          <AlertTriangle />
          <span>{error}</span>
          <button type="button" onClick={() => setError("")} aria-label="关闭错误提示"><X /></button>
        </div>
      )}
    </main>
  );
}
