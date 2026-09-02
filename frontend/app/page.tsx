"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import { useRouter } from "next/navigation";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import {
  AlertTriangle, ArrowDownRight, ArrowRight, ArrowUpRight, Bell, BookOpen, CalendarDays,
  CheckCircle2, ChevronDown, CircleDollarSign, ClipboardCheck, Clock3, Copy, Download,
  Eye, FileArchive, FileSpreadsheet, FolderOpen, GitCompareArrows, HardHat, Home,
  Landmark, Link2, Mail, Menu, MoreHorizontal, Paperclip, Plus, ReceiptText, RefreshCw,
  ScanLine, Settings, ShieldCheck, Sparkles, Upload, Users, X,
  Unlink, UserMinus, LogOut, MonitorSmartphone,
  Archive, ArchiveRestore, Trash2, RotateCcw,
} from "lucide-react";
import { API_BASE, ApiError, api, authHeaders } from "@/lib/api";
import { CheckRow, Modal, Money, StatusChip } from "@/components/ui";

type Tab = "overview" | "budget" | "changes" | "payments" | "evidence";
type Change = { id: string; change_type: "increase" | "decrease"; title: string; reason: string; content: string; amount_cents: number; status: string; version: number; area?: string; category: string; proposer: string; proposed_on: string; schedule_impact_days: number };
type TimelineItem = { id: string; event_type: string; title: string; detail: string; actor: string; amount_delta_cents: number; created_at: string };
type Milestone = { id: string; name: string; planned_amount_cents: number; planned_date: string; condition: string; required_acceptance: string; paid_cents: number; acceptance: null | { id?: string; result: string; accepted_on: string; open_issues: number; notes: string } };
type EvidenceItem = { id: string; original_name: string; mime_type: string; size_bytes: number; evidence_type: string; description: string; related_type?: string | null; related_id?: string | null; created_at: string };
type PaymentRecordData = { id: string; milestone_id: string; milestone_name: string; amount_cents: number; paid_on: string; payee: string; method: string; reference: string; record_type: "normal" | "reversal"; reversal_of_payment_id: string | null; controlled: boolean; override_reason: string | null; created_at: string };
type ToastTone = "success" | "warning" | "error";
type QuoteParseJob = { id: string; quote_id: string; status: "queued" | "running" | "succeeded" | "failed"; progress: number; stage: string; attempt: number; max_attempts: number; error_message: string | null; parse_method: string | null; created_at: string; started_at: string | null; finished_at: string | null; updated_at: string };
type QuoteSummary = { id: string; name: string; original_name: string; status: string; total_cents: number; source_total_cents: number | null; difference_cents: number | null; item_count: number; low_confidence_count: number; input_type: string; parse_method: string; page_count: number; warnings: string[]; error_message?: string; parse_job: QuoteParseJob | null };
type QuoteItemData = { id: string; original_name: string; standard_name: string; area: string | null; category: string; quantity: string | null; unit: string | null; unit_price_cents: number | null; total_cents: number; material_info: string | null; craft_notes: string | null; source_location: string; source_excerpt: string | null; confidence: number; field_confidences: Record<string, number> };
type QuoteDetail = QuoteSummary & { parser_version: string; correction_count: number; items: QuoteItemData[] };
type ComparisonGroup = { id: string; standard_name: string; category: string; area: string | null; match_type: "manual" | "suggested"; match_confidence: number; missing_quote_ids: string[]; price_spread_cents: number; items: Record<string, { id: string; quote_id: string; name: string; original_name: string; total_cents: number; unit_price_cents: number | null; quantity: string | null; unit: string | null; source_location: string }> };
type QuoteComparison = { quotes: { id: string; name: string; status: string; total_cents: number; item_count: number }[]; summary: { lowest_total_cents: number; highest_total_cents: number; total_spread_cents: number; matched_group_count: number; incomplete_group_count: number }; groups: ComparisonGroup[]; notice: string };
type SessionInfo = { user: { id: string; name: string; email: string }; memberships: { project_id: string; role: "owner" | "co_manager" | "viewer" }[]; mode: string; capabilities?: { uploads_enabled?: boolean } };
type MemberData = { current_role: "owner" | "co_manager" | "viewer"; limit: number; members: { id: string; user: { id: string; name: string; email: string }; role: "owner" | "co_manager" | "viewer"; status: string; created_at: string }[]; invites: { id: string; email: string; role: "co_manager" | "viewer"; status: string; expires_at: string; created_at: string }[] };
type InviteResult = { id: string; email: string; role: string; token: string; accept_path: string; expires_at: string };
type LoginDevice = { id: string; current: boolean; device: string; last_seen_at: string; created_at: string; expires_at: string };
type ProjectSettingsData = { project: { id: string; name: string; city: string; area_sqm: number; area_basis: string; renovation_type: string; address: string | null; notes: string; planned_start: string | null; planned_end: string | null; fund_limit_cents: number; reserve_cents: number; status: string; archived_at: string | null; deletion_scheduled_for: string | null }; role: "owner" | "co_manager" | "viewer"; categories: { id: string; name: string; planned_limit_cents: number | null; forecast_cents: number; sort_order: number }[]; fund_limit_history: { id: string; previous_cents: number | null; new_cents: number; reason: string; changed_by_name: string; created_at: string }[]; data_counts: { quotes: number; changes: number; payments: number; evidence: number } };
type Dashboard = {
  project: { id: string; name: string; city: string; area_sqm: number; area_basis: string; renovation_type: string; status: string; planned_end: string | null };
  budget: { fund_limit_cents: number; baseline_cents: number; baseline_version: number | null; approved_change_cents: number; approved_budget_cents: number; pending_risk_cents: number; predicted_settlement_cents: number; paid_cents: number; remaining_funds_cents: number; next_30_days_cents: number; approved_overrun_rate: number; predicted_overrun_rate: number; payment_progress: number | null };
  alerts: { code: string; level: string; title: string; action: string }[];
  changes: Change[];
  next_milestone: Milestone | null;
  timeline: TimelineItem[];
};

gsap.registerPlugin(ScrollTrigger);

const navItems: { id: Tab; label: string; icon: typeof Home }[] = [
  { id: "overview", label: "项目总览", icon: Home },
  { id: "budget", label: "报价与预算", icon: FileSpreadsheet },
  { id: "changes", label: "增减项", icon: ReceiptText },
  { id: "payments", label: "验收与付款", icon: ClipboardCheck },
  { id: "evidence", label: "证据与时间线", icon: FolderOpen },
];

function cn(...parts: (string | false | undefined)[]) { return parts.filter(Boolean).join(" "); }
function formatDate(value: string | null, withTime = false) { return value ? new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}) }).format(new Date(value)) : "待设置"; }
function today() {
  const value = new Date();
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
function requiresAcceptance(item: Milestone) {
  return !["", "无", "无需", "不需要", "不需验收", "无需验收", "不需要验收"].includes((item.required_acceptance || "").trim());
}
function errorMessage(reason: unknown, fallback: string) { return reason instanceof Error ? reason.message : fallback; }
function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
function formatMoneyText(cents: number) {
  return `¥${(cents / 100).toLocaleString("zh-CN", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}
type UploadCapabilityState = "enabled" | "demo_disabled" | "unavailable";
const uploadsDisabledMessage = "演示环境已关闭文件上传；完整部署可启用。";
const uploadsUnavailableMessage = "当前环境尚未开放文件上传能力；完整部署可启用。";

function uploadCapabilityMessage(state: UploadCapabilityState) {
  return state === "unavailable" ? uploadsUnavailableMessage : uploadsDisabledMessage;
}

function UploadCapabilityNotice({ state }: { state: Exclude<UploadCapabilityState, "enabled"> }) {
  const unavailable = state === "unavailable";
  return <section className="upload-capability-notice" role="status" aria-labelledby="upload-capability-title"><ShieldCheck aria-hidden="true" /><div><strong id="upload-capability-title">{unavailable ? "当前环境尚未开放文件上传能力" : "演示环境已关闭文件上传"}</strong><p>现有报价、证据和项目记录仍可正常查看；完整部署可启用上传。{unavailable ? "为避免在能力状态不明确时误接收文件，上传入口会默认关闭。" : "这是系统级演示配置，不代表账号被设为只读。"}</p></div></section>;
}
function withPaidCents(budget: Dashboard["budget"], paidCents: number): Dashboard["budget"] {
  return {
    ...budget,
    paid_cents: paidCents,
    payment_progress: budget.approved_budget_cents > 0 ? paidCents / budget.approved_budget_cents : null,
  };
}
const timelineFieldNames: Record<string, string> = {
  standard_name: "标准项目名称",
  area: "施工区域",
  category: "预算类别",
  quantity: "数量",
  unit: "单位",
  unit_price_cents: "单价",
  total_cents: "合计金额",
  material_info: "材料信息",
  craft_notes: "工艺备注",
};
function formatTimelineDetail(value: string) {
  return Object.entries(timelineFieldNames).reduce(
    (result, [field, label]) => result.replace(new RegExp(`\\b${field}\\b`, "g"), label),
    value,
  );
}

export default function HomePage() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("overview");
  const [projectId, setProjectId] = useState("");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [milestones, setMilestones] = useState<Milestone[]>([]);
  const [changes, setChanges] = useState<Change[]>([]);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [evidenceError, setEvidenceError] = useState("");
  const [payments, setPayments] = useState<PaymentRecordData[]>([]);
  const [paymentsLoading, setPaymentsLoading] = useState(true);
  const [paymentsError, setPaymentsError] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState<{ message: string; tone: ToastTone } | null>(null);
  const [changeModal, setChangeModal] = useState(false);
  const [paymentModal, setPaymentModal] = useState<Milestone | null>(null);
  const [acceptanceModal, setAcceptanceModal] = useState<Milestone | null>(null);
  const [evidenceModal, setEvidenceModal] = useState(false);
  const [milestoneModal, setMilestoneModal] = useState(false);
  const [reversePaymentRecord, setReversePaymentRecord] = useState<PaymentRecordData | null>(null);
  const [busy, setBusy] = useState(false);
  const [mobileMenu, setMobileMenu] = useState(false);
  const [sessionInfo, setSessionInfo] = useState<SessionInfo | null>(null);
  const [membersOpen, setMembersOpen] = useState(false);
  const [memberData, setMemberData] = useState<MemberData | null>(null);
  const [inviteLink, setInviteLink] = useState("");
  const [accountOpen, setAccountOpen] = useState(false);
  const [loginDevices, setLoginDevices] = useState<LoginDevice[]>([]);
  const [projectSettingsOpen, setProjectSettingsOpen] = useState(false);
  const [projectSettings, setProjectSettings] = useState<ProjectSettingsData | null>(null);
  const [notificationCount, setNotificationCount] = useState(0);
  const toastTimer = useRef<number | null>(null);
  const paymentDraftKey = useRef("");
  const reversalDraftKey = useRef("");
  const bootstrapStarted = useRef(false);
  const sidebarRef = useRef<HTMLElement>(null);
  const mobileMenuReturnFocus = useRef<HTMLButtonElement | null>(null);

  const flash = useCallback((message: string, tone: ToastTone = "success") => {
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
    setToast({ message, tone });
    toastTimer.current = window.setTimeout(() => { setToast(null); toastTimer.current = null; }, tone === "error" ? 4200 : 3000);
  }, []);
  useEffect(() => () => { if (toastTimer.current !== null) window.clearTimeout(toastTimer.current); }, []);

  const loadEvidence = useCallback(async (id: string) => {
    try {
      const items = await api<EvidenceItem[]>(`/projects/${id}/evidence`);
      setEvidence(Array.isArray(items) ? items : []);
      setEvidenceError("");
      return true;
    } catch (reason) {
      setEvidenceError(errorMessage(reason, "原始附件索引加载失败"));
      return false;
    }
  }, []);

  const loadPayments = useCallback(async (id: string) => {
    setPaymentsLoading(true);
    try {
      const items = await api<PaymentRecordData[]>(`/projects/${id}/payments`);
      setPayments(Array.isArray(items) ? items : []);
      setPaymentsError("");
      return true;
    } catch (reason) {
      setPaymentsError(errorMessage(reason, "付款流水加载失败"));
      return false;
    } finally {
      setPaymentsLoading(false);
    }
  }, []);

  const refresh = useCallback(async (id: string) => {
    if (!id) return;
    const [dash, milestoneData, changeData, timelineData] = await Promise.all([
      api<Dashboard>(`/projects/${id}/dashboard`),
      api<Milestone[]>(`/projects/${id}/milestones`),
      api<Change[]>(`/projects/${id}/changes`),
      api<TimelineItem[]>(`/projects/${id}/timeline`),
    ]);
    setDashboard(dash); setMilestones(milestoneData); setChanges(changeData); setTimeline(timelineData);
    void api<{ unread: number }>("/notifications/unread-count").then((state) => setNotificationCount(state.unread)).catch(() => setNotificationCount(0));
    void loadEvidence(id);
    void loadPayments(id);
  }, [loadEvidence, loadPayments]);

  useEffect(() => {
    if (bootstrapStarted.current) return;
    bootstrapStarted.current = true;
    (async () => {
      try {
        const auth = await api<{ authenticated: boolean }>("/auth/status");
        if (!auth.authenticated) { window.location.replace("/login"); return; }
        const [projects, session] = await Promise.all([api<{ id: string }[]>("/projects"), api<SessionInfo>("/session")]);
        if (!projects.length) { window.location.replace("/projects"); return; }
        const requestedProject = new URLSearchParams(window.location.search).get("project");
        const requestedTab = new URLSearchParams(window.location.search).get("tab") as Tab | null;
        if (requestedTab && navItems.some((item) => item.id === requestedTab)) setTab(requestedTab);
        const selectedProject = projects.find((item) => item.id === requestedProject) || projects[0];
        setSessionInfo(session);
        setProjectId(selectedProject.id);
        await refresh(selectedProject.id);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) { window.location.replace("/login"); return; }
        setError(e instanceof Error ? e.message : "加载失败");
      }
      finally { setLoading(false); }
    })();
  }, [refresh]);

  useEffect(() => {
    const handlePopState = () => {
      const requested = new URLSearchParams(window.location.search).get("tab") as Tab | null;
      setTab(requested && navItems.some((item) => item.id === requested) ? requested : "overview");
      window.scrollTo({ top: 0, behavior: "auto" });
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const selectTab = useCallback((next: Tab) => {
    setTab(next);
    setMobileMenu(false);
    const url = new URL(window.location.href);
    if (next === "overview") url.searchParams.delete("tab"); else url.searchParams.set("tab", next);
    window.history.pushState({ tab: next }, "", `${url.pathname}${url.search}${url.hash}`);
    window.scrollTo({ top: 0, behavior: "auto" });
  }, []);

  const openMobileMenu = useCallback((trigger: HTMLButtonElement) => {
    mobileMenuReturnFocus.current = trigger;
    setMobileMenu(true);
  }, []);

  const closeMobileMenu = useCallback(() => {
    setMobileMenu(false);
    window.requestAnimationFrame(() => mobileMenuReturnFocus.current?.focus());
  }, []);

  useEffect(() => {
    if (!mobileMenu) return;
    const menu = sidebarRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusable = () => Array.from(menu?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') || []);
    window.requestAnimationFrame(() => focusable()[0]?.focus());
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeMobileMenu(); return; }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0]; const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKey);
    return () => { document.body.style.overflow = previousOverflow; document.removeEventListener("keydown", handleKey); };
  }, [closeMobileMenu, mobileMenu]);

  const currentRole = sessionInfo?.memberships.find((item) => item.project_id === projectId)?.role || "viewer";
  const canWrite = currentRole !== "viewer" && !["已归档", "待删除"].includes(dashboard?.project.status || "");
  const uploadCapabilityState: UploadCapabilityState = sessionInfo?.capabilities?.uploads_enabled === true ? "enabled" : sessionInfo?.capabilities?.uploads_enabled === false ? "demo_disabled" : "unavailable";
  const uploadsEnabled = uploadCapabilityState === "enabled";
  const canManageMembers = currentRole === "owner";
  const canExport = currentRole === "owner" && !["待删除", "删除中"].includes(dashboard?.project.status || "");

  function openEvidenceUpload() {
    if (!uploadsEnabled) { flash(uploadCapabilityMessage(uploadCapabilityState), "warning"); return; }
    if (!canWrite) { flash("当前账号只有只读权限，不能上传证据", "error"); return; }
    setEvidenceModal(true);
  }

  async function syncAfterWrite(successMessage: string, staleMessage = "操作已保存，但页面数据暂未刷新") {
    try { await refresh(projectId); flash(successMessage); }
    catch { flash(staleMessage, "warning"); }
  }

  async function reloadMembersAfterWrite(successMessage: string) {
    try { setMemberData(await api<MemberData>(`/projects/${projectId}/members`)); flash(successMessage); }
    catch { flash(`${successMessage}，但成员列表暂未刷新`, "warning"); }
  }

  async function openMembers() {
    try { setMemberData(await api<MemberData>(`/projects/${projectId}/members`)); setMobileMenu(false); setMembersOpen(true); }
    catch (reason) { flash(errorMessage(reason, "成员信息加载失败"), "error"); }
  }

  async function inviteMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setInviteLink("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      const result = await api<InviteResult>(`/projects/${projectId}/invites`, { method: "POST", body: JSON.stringify({ email: form.get("email"), role: form.get("role") }) });
      setInviteLink(`${window.location.origin}${result.accept_path}`);
      formElement.reset();
      await reloadMembersAfterWrite("邀请已创建，请把安全链接发给家人");
    } catch (reason) { flash(errorMessage(reason, "邀请失败"), "error"); }
    finally { setBusy(false); }
  }

  async function updateMemberRole(membershipId: string, role: string) {
    if (!window.confirm("确认调整这位成员的项目权限？")) return;
    try { await api(`/project-memberships/${membershipId}`, { method: "PATCH", body: JSON.stringify({ role }) }); await reloadMembersAfterWrite("成员权限已更新"); }
    catch (reason) { flash(errorMessage(reason, "权限更新失败"), "error"); }
  }

  async function removeMember(membershipId: string) {
    if (!window.confirm("移除后，该成员将立即失去项目访问权。确认继续？")) return;
    try { await api(`/project-memberships/${membershipId}`, { method: "DELETE" }); await reloadMembersAfterWrite("成员已从项目移除"); }
    catch (reason) { flash(errorMessage(reason, "移除失败"), "error"); }
  }

  async function revokeInvite(inviteId: string) {
    if (!window.confirm("确认撤销这个邀请链接？")) return;
    try { await api(`/project-invites/${inviteId}/revoke`, { method: "POST" }); await reloadMembersAfterWrite("邀请已撤销"); }
    catch (reason) { flash(errorMessage(reason, "撤销失败"), "error"); }
  }

  async function openAccount() {
    try { setLoginDevices(await api<LoginDevice[]>("/auth/sessions")); setAccountOpen(true); setMobileMenu(false); }
    catch (reason) { flash(errorMessage(reason, "登录设备加载失败"), "error"); }
  }

  async function openProjectSettings() {
    try { setProjectSettings(await api<ProjectSettingsData>(`/projects/${projectId}/settings`)); setProjectSettingsOpen(true); setMobileMenu(false); }
    catch (reason) { flash(errorMessage(reason, "项目设置加载失败"), "error"); }
  }

  async function saveProjectSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const nextLimit = Math.round(Number(form.get("fund_limit")) * 100);
    const nextReserve = Math.round(Number(form.get("reserve") || 0) * 100);
    const plannedStart = String(form.get("planned_start") || "");
    const plannedEnd = String(form.get("planned_end") || "");
    const limitChanged = nextLimit !== projectSettings?.project.fund_limit_cents;
    const limitReason = String(form.get("fund_limit_reason") || "").trim();
    const reject = (message: string, field: string) => {
      flash(message, "error");
      (formElement.elements.namedItem(field) as HTMLElement | null)?.focus();
    };
    if (plannedStart && plannedEnd && plannedEnd < plannedStart) { reject("计划完工日期不能早于计划开工日期", "planned_end"); return; }
    if (nextReserve > nextLimit) { reject("风险预留金不能超过资金上限", "reserve"); return; }
    if (limitChanged && !limitReason) { reject("调整资金上限时，请填写修改原因", "fund_limit_reason"); return; }
    setBusy(true);
    try {
      const payload: Record<string, unknown> = {
        name: form.get("name"), city: form.get("city"), address: form.get("address") || null,
        area_sqm: Number(form.get("area_sqm")), area_basis: form.get("area_basis"), renovation_type: form.get("renovation_type"),
        planned_start: plannedStart || null, planned_end: plannedEnd || null,
        fund_limit_cents: nextLimit, reserve_cents: nextReserve, notes: form.get("notes"),
      };
      if (limitChanged) payload.fund_limit_reason = limitReason;
      setProjectSettings(await api<ProjectSettingsData>(`/projects/${projectId}`, { method: "PATCH", body: JSON.stringify(payload) }));
      await syncAfterWrite("项目资料与资金边界已更新", "项目设置已保存，但工作台暂未刷新");
    } catch (reason) { flash(errorMessage(reason, "项目设置保存失败"), "error"); }
    finally { setBusy(false); }
  }

  async function updateCategoryLimit(id: string, yuan: string) {
    try {
      await api(`/project-budget-categories/${id}`, { method: "PATCH", body: JSON.stringify({ planned_limit_cents: yuan === "" ? null : Math.round(Number(yuan) * 100) }) });
      try { setProjectSettings(await api<ProjectSettingsData>(`/projects/${projectId}/settings`)); flash("分类预算已更新"); }
      catch { flash("分类预算已更新，但设置列表暂未刷新", "warning"); }
    } catch (reason) { flash(errorMessage(reason, "分类预算更新失败"), "error"); }
  }

  async function lifecycleAction(action: "archive" | "reopen" | "deletion-cancel", message: string) {
    if (action === "archive" && !window.confirm("归档后项目将进入只读。确认归档？")) return;
    try {
      const result = await api<{ status: string }>(`/projects/${projectId}/${action}`, { method: "POST" });
      setDashboard((current) => current ? { ...current, project: { ...current.project, status: result.status } } : current);
      try { setProjectSettings(await api<ProjectSettingsData>(`/projects/${projectId}/settings`)); }
      catch { /* action already succeeded */ }
      await syncAfterWrite(message, `${message}，但页面数据暂未完全刷新`);
    } catch (reason) { flash(errorMessage(reason, "操作失败"), "error"); }
  }

  async function requestDeletion(projectName: string) {
    if (!window.confirm("项目将进入 7 天删除撤销期。确认继续？")) return;
    try {
      const result = await api<{ status: string; deletion_scheduled_for: string }>(`/projects/${projectId}/deletion-request`, { method: "POST", body: JSON.stringify({ project_name: projectName }) });
      setDashboard((current) => current ? { ...current, project: { ...current.project, status: result.status } } : current);
      try { setProjectSettings(await api<ProjectSettingsData>(`/projects/${projectId}/settings`)); }
      catch { /* request already succeeded */ }
      await syncAfterWrite("项目已进入 7 天删除撤销期", "删除申请已提交，但页面数据暂未完全刷新");
    } catch (reason) { flash(errorMessage(reason, "删除申请失败"), "error"); }
  }

  async function logout() {
    try { await api("/auth/logout", { method: "POST" }); }
    finally { window.location.replace("/login"); }
  }

  async function revokeDevice(id: string, current: boolean) {
    if (!window.confirm(current ? "确认退出当前账号？" : "确认让该设备立即退出？")) return;
    try {
      await api(`/auth/sessions/${id}`, { method: "DELETE" });
      if (current) { window.location.replace("/login"); return; }
      try { setLoginDevices(await api<LoginDevice[]>("/auth/sessions")); flash("该设备已经退出"); }
      catch { flash("该设备已退出，但设备列表暂未刷新", "warning"); }
    } catch (reason) { flash(errorMessage(reason, "设备退出失败"), "error"); }
  }

  async function createChange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true);
    const data = new FormData(event.currentTarget);
    let created: Change;
    try {
      created = await api<Change>(`/projects/${projectId}/changes`, { method: "POST", body: JSON.stringify({
        change_type: data.get("change_type"), title: data.get("title"), reason: data.get("reason"), content: data.get("content"),
        amount_cents: Math.round(Number(data.get("amount")) * 100), area: data.get("area") || null, category: data.get("category"), proposer: data.get("proposer"),
        proposed_on: today(), schedule_impact_days: Number(data.get("schedule_impact_days") || 0), no_attachment_acknowledged: data.get("no_attachment_acknowledged") === "on",
      }) });
    } catch (reason) {
      flash(errorMessage(reason, "增减项创建失败"), "error"); setBusy(false); return;
    }
    setChangeModal(false);
    setChanges((current) => [created, ...current.filter((item) => item.id !== created.id)]);
    setDashboard((current) => current ? { ...current, changes: [created, ...current.changes.filter((item) => item.id !== created.id)] } : current);
    let warning = "";
    try { await api(`/changes/${created.id}/actions/send`, { method: "POST", body: JSON.stringify({ comment: "由项目所有者发起确认" }) }); }
    catch { warning = "增减项已创建，但发起确认失败，记录已保留"; }
    try { await refresh(projectId); }
    catch { warning ||= "增减项已创建，但项目账本暂未刷新"; }
    flash(warning || "增减项已创建并进入待确认", warning ? "warning" : "success");
    setBusy(false);
  }

  async function changeAction(id: string, action: string) {
    const label = action === "approve" ? "批准" : action === "reject" ? "拒绝" : "发起确认";
    const confirmation = action === "send" ? "确认将这项草稿发起确认？系统会沿用当前记录，不会重复创建。" : `确认${label}这项预算变化？该操作会进入项目时间线。`;
    if (!window.confirm(confirmation)) return;
    setBusy(true);
    try {
      const updated = await api<Change>(`/changes/${id}/actions/${action}`, { method: "POST", body: JSON.stringify({ comment: action === "approve" ? "业主内部确认" : action === "reject" ? "业主内部拒绝" : "重新发起确认" }) });
      setChanges((current) => current.map((item) => item.id === id ? updated : item));
      await syncAfterWrite(action === "approve" ? "已批准，预算已更新" : action === "reject" ? "已拒绝这项变化" : "草稿已重新发起确认", `增减项已${label}，但项目账本暂未刷新`);
    } catch (reason) { flash(errorMessage(reason, "操作失败"), "error"); }
    finally { setBusy(false); }
  }

  async function saveAcceptance(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!acceptanceModal) return;
    const data = new FormData(event.currentTarget);
    const result = String(data.get("result"));
    const openIssues = Number(data.get("open_issues") || 0);
    if (result === "passed_with_issues" && openIssues < 1) { flash("带问题通过时，请填写至少 1 个未关闭问题", "error"); return; }
    setBusy(true);
    const milestoneId = acceptanceModal.id;
    const acceptance = { result, accepted_on: today(), notes: String(data.get("notes") || ""), open_issues: openIssues };
    try {
      await api(`/milestones/${milestoneId}/acceptances`, { method: "POST", body: JSON.stringify(acceptance) });
      setAcceptanceModal(null);
      setMilestones((current) => current.map((item) => item.id === milestoneId ? { ...item, acceptance } : item));
      setDashboard((current) => current?.next_milestone?.id === milestoneId ? { ...current, next_milestone: { ...current.next_milestone, acceptance } } : current);
      await syncAfterWrite("验收记录已保存", "验收记录已保存，但项目账本暂未刷新");
    } catch (reason) { flash(errorMessage(reason, "验收记录保存失败"), "error"); } finally { setBusy(false); }
  }

  function openPayment(item: Milestone) {
    if (!canWrite) { flash("当前账号只有只读权限，不能记录付款", "error"); return; }
    if (item.paid_cents >= item.planned_amount_cents) { flash("该付款节点已经付清，如需调整请先冲正原记录", "warning"); return; }
    paymentDraftKey.current = crypto.randomUUID();
    setPaymentModal(item);
  }

  function closePayment() {
    paymentDraftKey.current = "";
    setPaymentModal(null);
  }

  function openReversePayment(item: PaymentRecordData) {
    reversalDraftKey.current = crypto.randomUUID();
    setReversePaymentRecord(item);
  }

  function closeReversePayment() {
    reversalDraftKey.current = "";
    setReversePaymentRecord(null);
  }

  async function savePayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!paymentModal) return; setBusy(true);
    const data = new FormData(event.currentTarget);
    const milestoneId = paymentModal.id;
    const amountCents = Math.round(Number(data.get("amount")) * 100);
    const remainingCents = Math.max(0, paymentModal.planned_amount_cents - paymentModal.paid_cents);
    if (!Number.isFinite(amountCents) || amountCents <= 0 || amountCents > remainingCents) {
      flash(`本次付款应大于 0 且不超过节点剩余金额 ¥${(remainingCents / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}`, "error");
      setBusy(false); return;
    }
    if (!paymentDraftKey.current) paymentDraftKey.current = crypto.randomUUID();
    const paidOn = today();
    const payee = String(data.get("payee") || "");
    const method = String(data.get("method") || "");
    const reference = String(data.get("reference") || "");
    const overrideReason = String(data.get("override_reason") || "") || null;
    try {
      const result = await api<{ id: string; controlled: boolean }>(`/milestones/${milestoneId}/payments`, { method: "POST", body: JSON.stringify({ amount_cents: amountCents, paid_on: paidOn, payee, method, reference, override_reason: overrideReason, idempotency_key: paymentDraftKey.current }) });
      paymentDraftKey.current = "";
      setPaymentModal(null);
      setMilestones((current) => current.map((item) => item.id === milestoneId ? { ...item, paid_cents: item.paid_cents + amountCents } : item));
      setPayments((current) => [{ id: result.id, milestone_id: milestoneId, milestone_name: paymentModal.name, amount_cents: amountCents, paid_on: paidOn, payee, method, reference, record_type: "normal", reversal_of_payment_id: null, controlled: result.controlled, override_reason: overrideReason, created_at: new Date().toISOString() }, ...current.filter((item) => item.id !== result.id)]);
      setDashboard((current) => current ? { ...current, budget: withPaidCents(current.budget, current.budget.paid_cents + amountCents), next_milestone: current.next_milestone?.id === milestoneId ? { ...current.next_milestone, paid_cents: current.next_milestone.paid_cents + amountCents } : current.next_milestone } : current);
      await syncAfterWrite("已记录付款，项目总账已更新", "付款记录已保存，但项目总账暂未刷新");
    } catch (reason) { flash(errorMessage(reason, "付款记录保存失败"), "error"); } finally { setBusy(false); }
  }

  async function reversePayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!reversePaymentRecord || !canWrite) return;
    const reason = String(new FormData(event.currentTarget).get("reason") || "").trim();
    if (!reason) { flash("请填写冲正原因", "error"); return; }
    if (!window.confirm("冲正会以负向流水调整已付款金额，并永久保留原记录。确认继续？")) return;
    setBusy(true);
    const original = reversePaymentRecord;
    if (!reversalDraftKey.current) reversalDraftKey.current = crypto.randomUUID();
    const data = new FormData();
    data.append("reason", reason);
    data.append("idempotency_key", reversalDraftKey.current);
    try {
      const response = await fetch(`${API_BASE}/payments/${original.id}/reverse`, { method: "POST", body: data, headers: authHeaders(), credentials: "include" });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(body?.error?.message || "付款冲正失败");
      closeReversePayment();
      const reversed: PaymentRecordData = { id: body.id, milestone_id: original.milestone_id, milestone_name: original.milestone_name, amount_cents: original.amount_cents, paid_on: today(), payee: original.payee, method: original.method, reference: `冲正 ${original.id}`, record_type: "reversal", reversal_of_payment_id: original.id, controlled: false, override_reason: reason, created_at: new Date().toISOString() };
      setPayments((current) => [reversed, ...current.filter((item) => item.id !== reversed.id)]);
      setMilestones((current) => current.map((item) => item.id === original.milestone_id ? { ...item, paid_cents: Math.max(0, item.paid_cents - original.amount_cents) } : item));
      setDashboard((current) => current ? { ...current, budget: withPaidCents(current.budget, Math.max(0, current.budget.paid_cents - original.amount_cents)), next_milestone: current.next_milestone?.id === original.milestone_id ? { ...current.next_milestone, paid_cents: Math.max(0, current.next_milestone.paid_cents - original.amount_cents) } : current.next_milestone } : current);
      await syncAfterWrite("付款已冲正，原流水仍完整保留", "付款已冲正，但项目总账暂未刷新");
    } catch (reasonValue) { flash(errorMessage(reasonValue, "付款冲正失败"), "error"); }
    finally { setBusy(false); }
  }

  async function uploadEvidence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!uploadsEnabled) { setEvidenceModal(false); flash(uploadCapabilityMessage(uploadCapabilityState), "warning"); return; }
    if (!canWrite) { setEvidenceModal(false); flash("当前账号只有只读权限，不能上传证据", "error"); return; }
    setBusy(true); const data = new FormData(event.currentTarget);
    if (data.get("related_id")) data.set("related_type", "milestone");
    else { data.delete("related_id"); data.delete("related_type"); }
    try {
      const response = await fetch(`${API_BASE}/projects/${projectId}/evidence`, { method: "POST", body: data, headers: authHeaders(), credentials: "include" });
      if (!response.ok) { const payload = await response.json(); throw new Error(payload?.error?.message || "上传失败"); }
      const result = await response.json() as { id: string; original_name: string; size_bytes: number };
      setEvidenceModal(false);
      setEvidence((current) => [{ id: result.id, original_name: result.original_name, size_bytes: result.size_bytes, mime_type: (data.get("file") as File)?.type || "application/octet-stream", evidence_type: String(data.get("evidence_type") || "其他"), description: String(data.get("description") || ""), related_type: data.get("related_type") ? String(data.get("related_type")) : null, related_id: data.get("related_id") ? String(data.get("related_id")) : null, created_at: new Date().toISOString() }, ...current]);
      await syncAfterWrite("证据已加入项目时间线", "证据已上传，但项目时间线暂未刷新");
    } catch (reason) { flash(errorMessage(reason, "上传失败"), "error"); } finally { setBusy(false); }
  }

  async function createMilestone(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      const created = await api<Milestone>(`/projects/${projectId}/milestones`, { method: "POST", body: JSON.stringify({ name: data.get("name"), planned_amount_cents: Math.round(Number(data.get("planned_amount")) * 100), planned_date: data.get("planned_date"), condition: data.get("condition"), required_acceptance: data.get("required_acceptance") }) });
      setMilestoneModal(false);
      setMilestones((current) => [...current, created].sort((a, b) => a.planned_date.localeCompare(b.planned_date)));
      await syncAfterWrite("付款节点已创建", "付款节点已创建，但节点列表暂未刷新");
    } catch (reason) { flash(errorMessage(reason, "付款节点创建失败"), "error"); }
    finally { setBusy(false); }
  }

  async function downloadArchive() {
    if (currentRole !== "owner") { flash("只有项目所有者可以生成完整档案", "error"); return; }
    if (!canExport) { flash("删除撤销期不能生成新档案，请下载已有档案或先撤销删除申请", "warning"); return; }
    router.push(`/exports?project=${projectId}`);
  }

  const title = navItems.find((item) => item.id === tab)?.label || "项目总览";
  if (loading) return <main className="loading-screen"><div className="loading-mark">筑</div><p>正在整理项目账本…</p></main>;
  if (error || !dashboard) return <main className="error-screen"><div><AlertTriangle size={28} /><h1>暂时无法打开项目</h1><p>{error || "数据不存在"}</p><button className="button primary" onClick={() => location.reload()}><RefreshCw size={16} />重新加载</button></div></main>;

  return (
    <div className="app-shell workspace-v4">
      {mobileMenu && <button type="button" aria-label="关闭移动菜单" onClick={closeMobileMenu} style={{ position: "fixed", inset: 0, zIndex: 49, border: 0, background: "rgba(7, 22, 17, .36)" }} />}
      <aside ref={sidebarRef} className={cn("sidebar", mobileMenu && "mobile-open")} style={mobileMenu ? { zIndex: 60, paddingBottom: 82, overflowY: "auto" } : undefined}>
        <div className="brand"><div className="brand-mark">筑</div><div><strong>筑账</strong><span>装修预算管家</span></div><button className="mobile-close" onClick={closeMobileMenu} aria-label="关闭菜单"><X /></button></div>
        <button className="project-switcher" onClick={() => router.push("/projects")}><div className="project-avatar"><HardHat size={18} /></div><div><strong>{dashboard.project.name}</strong><span>{dashboard.project.city} · {dashboard.project.area_sqm}㎡</span></div><ChevronDown size={16} /></button>
        <nav className="side-nav" aria-label="项目导航">
          {navItems.map(({ id, label, icon: Icon }) => <button key={id} className={cn(tab === id && "active")} aria-current={tab === id ? "page" : undefined} onClick={() => selectTab(id)}><Icon size={19} /><span>{label}</span>{id === "changes" && changes.filter((c) => c.status === "pending_confirmation").length > 0 && <em>{changes.filter((c) => c.status === "pending_confirmation").length}</em>}</button>)}
        </nav>
        <div className="sidebar-bottom"><button onClick={() => router.push("/notifications")}><Bell size={18} />通知中心{notificationCount > 0 && <em>{notificationCount}</em>}</button><button onClick={openMembers}><Users size={18} />成员与权限</button><button onClick={openProjectSettings}><Settings size={18} />项目设置</button><button className="user-card" onClick={openAccount}><div className="avatar">{sessionInfo?.user.name.slice(0, 1) || "林"}</div><div><strong>{sessionInfo?.user.name || "林然"}</strong><span>{currentRole === "owner" ? "项目所有者" : currentRole === "co_manager" ? "共同管理者" : "只读成员"}</span></div><MoreHorizontal size={18} /></button></div>
      </aside>

      <main className="main-area overflow-x-hidden w-full max-w-full">
        <header className="topbar workspace-topbar">
          <button className="mobile-menu" onClick={(event) => openMobileMenu(event.currentTarget)} aria-label="打开菜单" aria-expanded={mobileMenu}><Menu /></button>
          <div className="topbar-context"><p className="breadcrumb">项目工作台 <span>/ {title}</span></p><h1>{dashboard.project.name}</h1></div>
          {canWrite ? <span className="workspace-project-status"><i />{dashboard.project.status}</span> : <span className="readonly-badge"><Eye size={13} />{dashboard.project.status === "已归档" ? "项目已归档" : dashboard.project.status === "待删除" ? "删除撤销期" : "只读浏览"}</span>}
          <div className="top-actions"><button className="icon-button notification" aria-label={`通知，${notificationCount} 条未读`} onClick={() => router.push("/notifications")}><Bell size={19} />{notificationCount > 0 && <span />}</button><button className="button secondary desktop-only" onClick={downloadArchive} disabled={busy || !canExport} title={!canExport ? "仅项目所有者可导出" : undefined}><Download size={16} />导出档案</button><button className="button primary" onClick={() => setChangeModal(true)} disabled={!canWrite}><Plus size={17} />新建增减项</button></div>
        </header>

        {uploadCapabilityState !== "enabled" && <UploadCapabilityNotice state={uploadCapabilityState} />}

        {tab === "overview" && <Overview dashboard={dashboard} canWrite={canWrite} uploadCapabilityState={uploadCapabilityState} canExport={canExport} onTab={selectTab} onAddChange={() => setChangeModal(true)} onUploadEvidence={openEvidenceUpload} onAcceptance={() => dashboard.next_milestone && !dashboard.next_milestone.acceptance && requiresAcceptance(dashboard.next_milestone) && setAcceptanceModal(dashboard.next_milestone)} onPayment={() => dashboard.next_milestone && openPayment(dashboard.next_milestone)} onExport={downloadArchive} />}
        {tab === "budget" && <BudgetView dashboard={dashboard} projectId={projectId} canWrite={canWrite} uploadCapabilityState={uploadCapabilityState} canExport={canExport} onExport={downloadArchive} onOpenTimeline={() => selectTab("evidence")} onChanged={() => refresh(projectId)} notify={flash} />}
        {tab === "changes" && <ChangesView changes={changes} busy={busy} canWrite={canWrite} onAdd={() => setChangeModal(true)} onAction={changeAction} />}
        {tab === "payments" && <PaymentsView milestones={milestones} payments={payments} paymentsLoading={paymentsLoading} paymentsError={paymentsError} canWrite={canWrite} onAdd={() => setMilestoneModal(true)} onRetry={() => void loadPayments(projectId)} onAcceptance={setAcceptanceModal} onPayment={openPayment} onReverse={openReversePayment} />}
        {tab === "evidence" && <EvidenceView timeline={timeline} milestones={milestones} evidence={evidence} evidenceError={evidenceError} canWrite={canWrite} uploadCapabilityState={uploadCapabilityState} canExport={canExport} onRetry={() => void loadEvidence(projectId)} onUpload={openEvidenceUpload} onExport={downloadArchive} />}
      </main>

      <nav className="mobile-bottom" aria-label="移动端导航">{navItems.slice(0, 4).map(({ id, label, icon: Icon }) => <button key={id} className={cn(tab === id && "active")} aria-current={tab === id ? "page" : undefined} onClick={() => selectTab(id)}><Icon size={20} /><span>{label.replace("项目", "").replace("验收与", "")}</span></button>)}<button onClick={(event) => openMobileMenu(event.currentTarget)} aria-expanded={mobileMenu}><Menu size={20} /><span>更多</span></button></nav>

      <Modal open={changeModal} onClose={() => setChangeModal(false)} eyebrow="先确认，再施工" title="记录一项预算变化"><form className="modal-form" onSubmit={createChange}><div className="segmented"><label><input type="radio" name="change_type" value="increase" defaultChecked /><span><ArrowUpRight size={16} />增加项</span></label><label><input type="radio" name="change_type" value="decrease" /><span><ArrowDownRight size={16} />减少项</span></label></div><label className="field wide"><span>变更标题</span><input name="title" required placeholder="例如：厨房墙面找平追加" /></label><div className="form-grid"><label className="field"><span>金额（元）</span><div className="money-input"><b>¥</b><input name="amount" required type="number" min="0.01" step="0.01" placeholder="0.00" /></div></label><label className="field"><span>房间 / 区域</span><input name="area" placeholder="例如：厨房" /></label></div><div className="form-grid"><label className="field"><span>预算类别</span><select name="category" defaultValue="其他"><option>拆除与新建</option><option>水电</option><option>泥瓦</option><option>木作</option><option>油漆</option><option>门窗</option><option>厨卫</option><option>主材</option><option>软装</option><option>家具家电</option><option>设计与管理</option><option>其他</option></select></label><label className="field"><span>提出人</span><select name="proposer" defaultValue="施工方"><option>施工方</option><option>业主</option><option>设计师</option><option>监理</option></select></label></div><label className="field wide"><span>为什么发生变化</span><input name="reason" required placeholder="写清现场原因，便于之后核对" /></label><label className="field wide"><span>具体施工范围</span><textarea name="content" required rows={3} placeholder="包含哪些施工、材料或工艺？" /></label><label className="field wide"><span>工期变化（天）</span><input name="schedule_impact_days" type="number" defaultValue="0" /></label><label className="attachment-ack"><input name="no_attachment_acknowledged" type="checkbox" required /><Paperclip size={17} /><span><strong>我确认当前没有附件</strong><small>{uploadsEnabled ? "创建后可在证据库补充现场照片或报价单。" : uploadCapabilityState === "unavailable" ? "当前环境尚未开放上传；完整部署可补充附件。" : "演示环境已关闭上传；完整部署可补充附件。"}</small></span></label><div className="modal-actions"><button type="button" className="button ghost" onClick={() => setChangeModal(false)}>取消</button><button className="button primary" disabled={busy}>{busy ? "正在保存…" : "创建并发起确认"}</button></div></form></Modal>

      <Modal open={!!acceptanceModal} onClose={() => setAcceptanceModal(null)} eyebrow={acceptanceModal?.name} title="记录阶段验收"><form className="modal-form" onSubmit={saveAcceptance}><label className="field wide"><span>验收结果</span><select name="result" defaultValue="passed"><option value="passed">通过</option><option value="passed_with_issues">带问题通过</option><option value="failed">不通过</option></select></label><label className="field wide"><span>验收说明</span><textarea name="notes" rows={4} placeholder="记录现场事实，不代表平台专业鉴定" /></label><label className="field wide"><span>未关闭问题数量</span><input name="open_issues" type="number" min="0" defaultValue="0" /></label><div className="legal-note"><ShieldCheck size={17} />平台只保留你的验收事实记录，不自动判断工程质量。</div><div className="modal-actions"><button type="button" className="button ghost" onClick={() => setAcceptanceModal(null)}>取消</button><button className="button primary" disabled={busy}>保存验收记录</button></div></form></Modal>

      <PaymentModal milestone={paymentModal} onClose={closePayment} onSubmit={savePayment} busy={busy} />

      <Modal open={milestoneModal} onClose={() => setMilestoneModal(false)} eyebrow="付款计划" title="新增一个付款节点"><form className="modal-form" onSubmit={createMilestone}><div className="form-grid"><label className="field"><span>节点名称</span><input name="name" required placeholder="例如：水电阶段款" /></label><label className="field"><span>计划金额（元）</span><input name="planned_amount" type="number" min="0.01" step="0.01" required /></label><label className="field"><span>计划日期</span><input name="planned_date" type="date" min={today()} required /></label><label className="field"><span>验收要求</span><select name="required_acceptance" defaultValue="阶段验收记录"><option>阶段验收记录</option><option value="无">无需验收</option></select></label></div><label className="field wide"><span>付款条件</span><textarea name="condition" rows={3} required placeholder="写清什么事实完成后进入这笔付款" /></label><div className="modal-actions"><button type="button" className="button ghost" onClick={() => setMilestoneModal(false)}>取消</button><button className="button primary" disabled={busy}>{busy ? "正在创建…" : "创建付款节点"}</button></div></form></Modal>

      <Modal open={!!reversePaymentRecord} onClose={closeReversePayment} eyebrow={reversePaymentRecord?.milestone_name} title="冲正一笔付款"><form className="modal-form" onSubmit={reversePayment}><div className="legal-note"><AlertTriangle size={17} />冲正不会删除原流水，而会增加一条等额负向记录并调整节点已付款金额。</div><label className="field wide"><span>原付款</span><input readOnly value={reversePaymentRecord ? `${formatDate(reversePaymentRecord.paid_on)} · ${reversePaymentRecord.payee} · ¥${(reversePaymentRecord.amount_cents / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}` : ""} /></label><label className="field wide"><span>冲正原因</span><textarea name="reason" required rows={4} placeholder="例如：重复登记、金额录入错误或款项已退回" /></label><div className="modal-actions"><button type="button" className="button ghost" onClick={closeReversePayment}>取消</button><button className="button primary" disabled={busy}>{busy ? "正在冲正…" : "确认冲正并保留原记录"}</button></div></form></Modal>

      <MemberPanel open={membersOpen} data={memberData} currentUserId={sessionInfo?.user.id || ""} canManage={canManageMembers} busy={busy} inviteLink={inviteLink} onClose={() => { setMembersOpen(false); setInviteLink(""); }} onInvite={inviteMember} onCopy={() => { if (inviteLink) void navigator.clipboard.writeText(inviteLink).then(() => flash("邀请链接已复制")).catch(() => flash("复制失败，请手动选择链接", "error")); }} onRole={updateMemberRole} onRemove={removeMember} onRevokeInvite={revokeInvite} />

      <AccountPanel open={accountOpen} user={sessionInfo?.user || null} devices={loginDevices} onClose={() => setAccountOpen(false)} onLogout={logout} onRevoke={revokeDevice} />

      <ProjectSettingsPanel open={projectSettingsOpen} data={projectSettings} busy={busy} canExport={canExport} onClose={() => setProjectSettingsOpen(false)} onSave={saveProjectSettings} onCategory={updateCategoryLimit} onArchive={() => lifecycleAction("archive", "项目已归档，当前保持只读")} onReopen={() => lifecycleAction("reopen", "项目已重新开启")} onDelete={requestDeletion} onCancelDelete={() => lifecycleAction("deletion-cancel", "删除申请已撤销")} onExport={downloadArchive} />

      <Modal open={evidenceModal && canWrite && uploadsEnabled} onClose={() => setEvidenceModal(false)} eyebrow="原件是依据" title="上传项目证据"><form className="modal-form" onSubmit={uploadEvidence}><label className="upload-zone"><Upload size={26} /><strong>选择照片或文档</strong><span>JPG、PNG、HEIC、PDF、XLSX、DOCX、CSV，最大 30 MB</span><input name="file" type="file" required accept=".jpg,.jpeg,.png,.heic,.pdf,.xlsx,.docx,.csv" /></label><div className="form-grid"><label className="field"><span>证据类型</span><select name="evidence_type"><option>现场照片</option><option>合同</option><option>付款凭证</option><option>报价单</option><option>其他</option></select></label><label className="field"><span>关联付款节点（可选）</span><select name="related_id" defaultValue=""><option value="">不关联具体节点</option>{milestones.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="field wide"><span>说明</span><input name="description" placeholder="这份文件记录了什么" /></label></div><div className="modal-actions"><button type="button" className="button ghost" onClick={() => setEvidenceModal(false)}>取消</button><button className="button primary" disabled={busy}>上传并保留原件</button></div></form></Modal>

      {toast && <div className="toast" role={toast.tone === "error" ? "alert" : "status"} data-tone={toast.tone}>{toast.tone === "success" ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} style={{ color: toast.tone === "error" ? "#f2a38f" : "#e8c778" }} />}{toast.message}</div>}
    </div>
  );
}

function ProjectSettingsPanel({ open, data, busy, canExport, onClose, onSave, onCategory, onArchive, onReopen, onDelete, onCancelDelete, onExport }: { open: boolean; data: ProjectSettingsData | null; busy: boolean; canExport: boolean; onClose: () => void; onSave: (event: FormEvent<HTMLFormElement>) => void; onCategory: (id: string, yuan: string) => void; onArchive: () => void; onReopen: () => void; onDelete: (projectName: string) => void; onCancelDelete: () => void; onExport: () => void }) {
  const [deleteName, setDeleteName] = useState("");
  const canManage = data?.role === "owner";
  const status = data?.project.status;
  const closePanel = () => { setDeleteName(""); onClose(); };
  return <Modal open={open} onClose={closePanel} eyebrow="项目设置" title="让项目边界始终可解释" className="project-settings-dialog">
    {!data ? <div className="inline-loading">正在读取项目设置…</div> : <div className="project-settings-panel">
      <section className="settings-overview"><div><span>{data.project.status}</span><h3>{data.project.name}</h3><p>{data.project.city} · {data.project.area_sqm}㎡ · {data.project.renovation_type}</p></div><div>{Object.entries(data.data_counts).map(([key, value]) => <article key={key}><strong>{value}</strong><span>{{ quotes: "报价", changes: "增减项", payments: "付款", evidence: "证据" }[key as keyof typeof data.data_counts]}</span></article>)}</div></section>

      {status === "待删除" && <section className="deletion-countdown"><RotateCcw /><div><strong>项目正在 7 天撤销期内</strong><p>{data.project.deletion_scheduled_for ? `${new Date(data.project.deletion_scheduled_for).toLocaleString("zh-CN")} 后删除业务数据和原始附件。` : "删除时间正在计算。"}</p></div>{canManage && <button onClick={onCancelDelete}>撤销删除</button>}</section>}

      <form className="settings-form" key={`${data.project.id}-${data.project.fund_limit_cents}`} onSubmit={onSave}><div className="settings-section-head"><div><p>项目资料</p><h3>房子和施工计划</h3></div><span>{canManage && !["已归档", "待删除"].includes(status || "") ? "可编辑" : "只读"}</span></div><div className="settings-fields"><label><span>项目名称</span><input name="name" required defaultValue={data.project.name} disabled={!canManage || status !== "施工中" && status !== "准备中" && status !== "待结算"} /></label><label><span>所在城市</span><input name="city" required defaultValue={data.project.city} disabled={!canManage || ["已归档", "待删除"].includes(status || "")} /></label><label><span>小区或地址</span><input name="address" defaultValue={data.project.address || ""} disabled={!canManage || ["已归档", "待删除"].includes(status || "")} /></label><label><span>面积</span><input name="area_sqm" type="number" min="1" required defaultValue={data.project.area_sqm} disabled={!canManage || ["已归档", "待删除"].includes(status || "")} /></label><label><span>面积口径</span><select name="area_basis" defaultValue={data.project.area_basis} disabled={!canManage || ["已归档", "待删除"].includes(status || "")}><option>套内面积</option><option>建筑面积</option></select></label><label><span>装修方式</span><select name="renovation_type" defaultValue={data.project.renovation_type} disabled={!canManage || ["已归档", "待删除"].includes(status || "")}><option>清包</option><option>半包</option><option>全包</option><option>整装</option></select></label><label><span>计划开工</span><input name="planned_start" type="date" defaultValue={data.project.planned_start || ""} disabled={!canManage || ["已归档", "待删除"].includes(status || "")} /></label><label><span>计划完工</span><input name="planned_end" type="date" defaultValue={data.project.planned_end || ""} disabled={!canManage || ["已归档", "待删除"].includes(status || "")} /></label><label><span>资金上限（元）</span><input name="fund_limit" type="number" min="1" required defaultValue={data.project.fund_limit_cents / 100} disabled={!canManage || ["已归档", "待删除"].includes(status || "")} /></label><label><span>风险预留金（元）</span><input name="reserve" type="number" min="0" defaultValue={data.project.reserve_cents / 100} disabled={!canManage || ["已归档", "待删除"].includes(status || "")} /></label><label className="wide"><span>资金上限修改原因</span><input name="fund_limit_reason" placeholder="仅在资金上限变化时必填" disabled={!canManage || ["已归档", "待删除"].includes(status || "")} /></label><label className="wide"><span>项目备注</span><textarea name="notes" rows={3} defaultValue={data.project.notes} disabled={!canManage || ["已归档", "待删除"].includes(status || "")} /></label></div>{canManage && !["已归档", "待删除"].includes(status || "") && <div className="settings-save"><button disabled={busy}>保存项目设置</button></div>}</form>

      <section className="category-settings"><div className="settings-section-head"><div><p>分类预算</p><h3>12 类默认边界</h3></div><span>失焦后保存</span></div><div className="category-limit-grid">{data.categories.map((category) => <label key={`${category.id}-${category.planned_limit_cents}-${category.forecast_cents}`}><span>{category.name}</span><small>已知预测 <Money cents={category.forecast_cents} /></small><div><b>¥</b><input type="number" min="0" defaultValue={category.planned_limit_cents === null ? "" : category.planned_limit_cents / 100} placeholder="未设置上限" disabled={!canManage || ["已归档", "待删除"].includes(status || "")} onBlur={(event) => { const current = category.planned_limit_cents === null ? "" : String(category.planned_limit_cents / 100); if (event.target.value !== current) onCategory(category.id, event.target.value); }} /></div></label>)}</div></section>

      <section className="fund-history"><div className="settings-section-head"><div><p>资金历史</p><h3>每次调整都有来由</h3></div><span>{data.fund_limit_history.length} 个版本</span></div>{data.fund_limit_history.map((item) => <article key={item.id}><div><strong><Money cents={item.new_cents} /></strong><span>{item.previous_cents === null ? "首次设置" : <><Money cents={item.previous_cents} /> → 当前版本</>}</span></div><p>{item.reason}</p><small>{item.changed_by_name} · {formatDate(item.created_at, true)}</small></article>)}</section>

      <section className="lifecycle-settings"><div className="settings-section-head"><div><p>项目生命周期</p><h3>归档不是删除</h3></div></div><div className="lifecycle-actions"><article><Archive /><div><strong>{status === "已归档" ? "重新开启项目" : "归档为只读"}</strong><p>归档后仍可查看和导出，历史记录保持不变。</p></div>{canManage && status !== "待删除" && <button onClick={status === "已归档" ? onReopen : onArchive}>{status === "已归档" ? <><ArchiveRestore />重新开启</> : "归档项目"}</button>}</article><article><Download /><div><strong>{canExport ? "先带走完整档案" : "删除撤销期不再生成新档案"}</strong><p>{canExport ? "在归档或删除前，建议保留 PDF、CSV 与原始附件。" : "可以查看项目或下载仍在有效期内的已有档案。"}</p></div><button onClick={onExport} disabled={!canExport}>{canExport ? "导出项目" : "暂不可生成"}</button></article>{canManage && status !== "待删除" && <article className="danger-zone"><Trash2 /><div><strong>申请删除项目</strong><p>输入完整项目名称确认。提交后有 7 天可以撤销。</p><input value={deleteName} onChange={(event) => setDeleteName(event.target.value)} placeholder={data.project.name} /></div><button disabled={deleteName !== data.project.name} onClick={() => onDelete(deleteName)}>进入撤销期</button></article>}</div></section>
    </div>}
  </Modal>;
}

function AccountPanel({ open, user, devices, onClose, onLogout, onRevoke }: { open: boolean; user: SessionInfo["user"] | null; devices: LoginDevice[]; onClose: () => void; onLogout: () => void; onRevoke: (id: string, current: boolean) => void }) {
  return <Modal open={open} onClose={onClose} eyebrow="账号与设备" title="登录权只留在你信任的设备上" className="account-dialog">
    <div className="account-panel">
      <section className="account-identity"><div className="account-avatar">{user?.name.slice(0, 1) || "筑"}</div><div><p>已验证邮箱</p><h3>{user?.name}</h3><span>{user?.email}</span></div><ShieldCheck /></section>
      <section className="device-section"><div className="member-section-head"><div><p>登录设备</p><h3>最近使用的会话</h3></div><span>{devices.length} 台</span></div><div className="device-list">{devices.map((device) => <article key={device.id}><MonitorSmartphone /><div><strong>{device.current ? "当前设备" : "已登录设备"}</strong><small>{device.device}</small><span>最近活动 {formatDate(device.last_seen_at, true)}</span></div><button onClick={() => onRevoke(device.id, device.current)}>{device.current ? "退出" : "移除"}</button></article>)}</div></section>
      <div className="account-actions"><p>成员管理、设备移除等敏感操作会要求最近 15 分钟内完成过邮箱验证。</p><button onClick={onLogout}><LogOut />退出当前账号</button></div>
    </div>
  </Modal>;
}

function MemberPanel({ open, data, currentUserId, canManage, busy, inviteLink, onClose, onInvite, onCopy, onRole, onRemove, onRevokeInvite }: { open: boolean; data: MemberData | null; currentUserId: string; canManage: boolean; busy: boolean; inviteLink: string; onClose: () => void; onInvite: (event: FormEvent<HTMLFormElement>) => void; onCopy: () => void; onRole: (id: string, role: string) => void; onRemove: (id: string) => void; onRevokeInvite: (id: string) => void }) {
  const scope = useRef<HTMLDivElement>(null);
  const roleNames = { owner: "项目所有者", co_manager: "共同管理者", viewer: "只读成员" };
  useGSAP(() => {
    if (!open || !data) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      gsap.set(".member-avatar, .permission-word", { clearProps: "all" });
      return;
    }
    gsap.fromTo(".member-avatar", { scale: .8, opacity: 0 }, { scale: 1, opacity: 1, duration: .65, stagger: .07, ease: "power3.out" });
    gsap.fromTo(".permission-word", { opacity: .12 }, { opacity: 1, stagger: .05, ease: "none", scrollTrigger: { trigger: ".permission-copy", start: "top 82%", end: "bottom 54%", scrub: true } });
  }, { scope, dependencies: [open, data?.members.length] });

  return <Modal open={open} onClose={onClose} eyebrow="成员与权限" title="把同一份事实，交给家里重要的人" className="member-dialog">
    <div className="member-panel" ref={scope}>
      {!data ? <div className="inline-loading">正在读取成员与权限…</div> : <>
        <section className="member-bento"><div className="member-intro"><p>当前协作空间</p><h3>{data.members.length}<span> / {data.limit + 1}</span></h3><div className="member-avatar-stack" aria-label={`${data.members.length} 位项目成员`}>{data.members.map((member) => <span className="member-avatar" key={member.id}>{member.user.name.slice(0, 1)}</span>)}</div><small>所有者加最多 {data.limit} 位家庭协作者</small></div><div className="permission-copy"><p>{"共同管理者可以编辑和确认 只读成员只看不改 所有成员看到同一份项目事实".split(" ").map((word) => <span className="permission-word" key={word}>{word} </span>)}</p></div><div className="permission-accordions"><article><strong>共同管理者</strong><span>查看全部 · 编辑业务 · 确认增减项</span></article><article><strong>只读成员</strong><span>查看全部 · 不可修改 · 不可确认</span></article><article><strong>项目所有者</strong><span>管理成员 · 导出项目 · 保留最高权限</span></article></div></section>
        <div className="permission-marquee" aria-label="协作能力"><div>{["预算同步", "变更共识", "付款核对", "证据共用", "权限留痕", "预算同步", "变更共识", "付款核对", "证据共用", "权限留痕"].map((text, index) => <span key={`${text}-${index}`}>{text}<i /></span>)}</div></div>
        <section className="member-directory"><div className="member-section-head"><div><p>项目成员</p><h3>谁可以看到和修改</h3></div><span>{data.members.length - 1} / {data.limit} 位协作者</span></div><div className="member-list">{data.members.map((member) => <article className="member-person" key={member.id}><div className="member-avatar">{member.user.name.slice(0, 1)}</div><div><strong>{member.user.name}{member.user.id === currentUserId && <em>当前账号</em>}</strong><small>{member.user.email}</small></div>{member.role === "owner" || !canManage ? <span className="member-role-label">{roleNames[member.role]}</span> : <select aria-label={`设置 ${member.user.name} 的权限`} value={member.role} onChange={(event) => onRole(member.id, event.target.value)}><option value="co_manager">共同管理者</option><option value="viewer">只读成员</option></select>}<div className="member-row-actions">{canManage && member.role !== "owner" && <button className="remove" aria-label={`移除 ${member.user.name}`} onClick={() => onRemove(member.id)}><UserMinus /></button>}</div></article>)}</div></section>
        {canManage && <section className="member-invite"><div><Mail /><p>邀请一位家庭协作者</p><h3>链接只在 7 天内有效</h3></div><form onSubmit={onInvite}><label className="field"><span>邮箱</span><input name="email" type="email" required placeholder="family@example.com" /></label><label className="field"><span>项目角色</span><select name="role" defaultValue="viewer"><option value="viewer">只读成员</option><option value="co_manager">共同管理者</option></select></label><button className="member-invite-button" disabled={busy}>创建安全邀请</button></form>{inviteLink && <div className="invite-link"><span>{inviteLink}</span><button onClick={onCopy}><Copy />复制链接</button></div>}</section>}
        {data.invites.length > 0 && <section className="pending-invites"><p>待接受邀请</p>{data.invites.map((invite) => <article key={invite.id}><div><strong>{invite.email}</strong><small>{roleNames[invite.role]} · {invite.status === "expired" ? "已过期" : `${new Date(invite.expires_at).toLocaleDateString("zh-CN")} 前有效`}</small></div>{canManage && invite.status !== "expired" && <button onClick={() => onRevokeInvite(invite.id)}>撤销</button>}</article>)}</section>}
        <aside className="demo-identity-note"><ShieldCheck /><p><strong>权限由服务端会话确认</strong>成员身份不能在浏览器中切换；邀请链接接受后会建立独立安全会话。</p></aside>
      </>}
    </div>
  </Modal>;
}

function Overview({ dashboard, canWrite, uploadCapabilityState, canExport, onTab, onAddChange, onUploadEvidence, onAcceptance, onPayment, onExport }: { dashboard: Dashboard; canWrite: boolean; uploadCapabilityState: UploadCapabilityState; canExport: boolean; onTab: (tab: Tab) => void; onAddChange: () => void; onUploadEvidence: () => void; onAcceptance: () => void; onPayment: () => void; onExport: () => void }) {
  const { budget, project } = dashboard;
  const scope = useRef<HTMLElement>(null);
  const predictedRatio = budget.fund_limit_cents > 0 ? budget.predicted_settlement_cents / budget.fund_limit_cents : 0;
  const paidToLimitRatio = budget.fund_limit_cents > 0 ? budget.paid_cents / budget.fund_limit_cents : 0;
  const paidMarkerPercent = Math.max(0, Math.min(100, paidToLimitRatio * 100));
  const paidMarkerEdge = paidMarkerPercent <= 6 ? "at-start" : paidMarkerPercent >= 94 ? "at-end" : "";
  const isOverLimit = budget.predicted_settlement_cents > budget.fund_limit_cents;
  const headroom = budget.fund_limit_cents - budget.predicted_settlement_cents;
  const nextMilestonePayable = !!dashboard.next_milestone && dashboard.next_milestone.paid_cents < dashboard.next_milestone.planned_amount_cents;
  const hasChanges = dashboard.changes.length > 0;
  const hasTimeline = dashboard.timeline.length > 0;

  useGSAP(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      gsap.set(".control-hero-word, .control-grid > article", { clearProps: "all" });
      return;
    }
    gsap.fromTo(".control-hero-word", { y: 12, opacity: .72 }, { y: 0, opacity: 1, duration: .48, stagger: .05, ease: "power3.out" });
    gsap.fromTo(".control-grid > article", { y: 14, opacity: .84 }, { y: 0, opacity: 1, duration: .52, stagger: .06, delay: .08, ease: "power3.out" });
  }, { scope });

  return <section ref={scope} className="control-overview overflow-x-hidden w-full max-w-full">
    <section className="control-hero" aria-labelledby="overview-title">
      <div className="control-project"><span aria-hidden="true" />{project.name} · {project.status} · {project.city} {project.area_sqm}㎡</div>
      <h2 id="overview-title"><span className="control-hero-word">下一笔钱，</span><span className="control-hero-word">先过付款检查。</span></h2>
      <p>{isOverLimit ? "预测结算已经超过资金上限，先处理待确认费用，再进入下一付款节点。" : "当前预测仍在资金上限内。付款前继续核对增减项、验收结果和原始凭证。"}</p>
      <div className="control-hero-actions"><button onClick={() => dashboard.next_milestone ? onPayment() : onTab("payments")} disabled={!!dashboard.next_milestone && (!canWrite || !nextMilestonePayable)} title={!canWrite ? "当前为只读项目" : !nextMilestonePayable && dashboard.next_milestone ? "该节点已付清" : undefined}><ShieldCheck />{dashboard.next_milestone && !nextMilestonePayable ? "下一节点已付清" : "检查下一笔付款"}</button><button onClick={onAddChange} disabled={!canWrite}><Plus />记录现场变化</button></div>
      <div className="control-hero-date"><CalendarDays />预计 {formatDate(project.planned_end)} 完工</div>
    </section>

    <section className="control-status" aria-label="项目关键状态">
      <article><span>已批准预算</span><Money cents={budget.approved_budget_cents} /></article>
      <article className={budget.pending_risk_cents > 0 ? "warning" : ""}><span>待确认风险</span><Money cents={budget.pending_risk_cents} sign /></article>
      <article><span>未来 30 天计划支出</span><Money cents={budget.next_30_days_cents} /></article>
      <article><span>付款前提醒</span><strong>{dashboard.alerts.length} 项</strong></article>
    </section>

    <div className="control-main">
      <header className="control-intro"><div><p>现在需要你判断的事情</p><h2>{isOverLimit ? "先收住风险，再继续付款。" : "预算有余量，但每一笔仍要有依据。"}</h2></div><button onClick={() => onTab("budget")}>打开完整预算账 <ArrowRight /></button></header>
      <section className="control-grid" aria-label="预算位置、风险与下一次决策">
        <article className="control-budget">
          <header><div><span>预测结算</span><Money cents={budget.predicted_settlement_cents} /></div><span className={isOverLimit ? "over" : "safe"}>{isOverLimit ? "预计超出上限" : "仍在资金上限内"}</span></header>
          <div className="control-budget-track" role="img" aria-label={`预测结算占资金上限 ${Math.round(predictedRatio * 100)}%，已支付 ${formatMoneyText(budget.paid_cents)}，占资金上限 ${Math.round(paidToLimitRatio * 100)}%`}><i style={{ width: `${Math.max(0, Math.min(100, predictedRatio * 100))}%` }} /><b className={paidMarkerEdge} style={{ left: `${paidMarkerPercent}%` }}><span>已付 {formatMoneyText(budget.paid_cents)}</span></b></div>
          <div className="control-budget-scale"><span>¥0</span><span>资金上限 <Money cents={budget.fund_limit_cents} /></span></div>
          <div className="control-budget-metrics"><article><span>{budget.baseline_version === null ? "合同基线 · 尚未确认" : `合同基线 · V${budget.baseline_version}`}</span><Money cents={budget.baseline_cents} /></article><article><span>已批准变更</span><Money cents={budget.approved_change_cents} sign /></article><article className="risk"><span>待确认风险</span><Money cents={budget.pending_risk_cents} sign /></article><article><span>已经支付</span><Money cents={budget.paid_cents} /></article><article><span>{isOverLimit ? "预计超支" : "预计余量"}</span><Money cents={Math.abs(headroom)} /></article><article><span>已付占已批准预算</span><strong>{budget.payment_progress === null ? "—" : `${Math.round(budget.payment_progress * 100)}%`}</strong></article></div>
          <button className="control-inline-link" onClick={() => onTab("budget")}>查看报价、基线与分类预算 <ArrowRight /></button>
        </article>

        <article className="control-risk">
          <header><div><span>付款前提醒</span><h3>{dashboard.alerts.length ? `${dashboard.alerts.length} 项需要处理` : "当前没有待处理风险"}</h3></div><AlertTriangle /></header>
          <div>{dashboard.alerts.length ? dashboard.alerts.slice(0, 3).map((alert) => <button key={`${alert.code}-${alert.title}`} onClick={() => onTab(alert.code === "A1" || alert.code === "A7" ? "changes" : alert.code === "A6" || alert.code === "A8" ? "payments" : "budget")}><i className={alert.level} /><span><strong>{alert.title}</strong><small>{alert.action}</small></span><ArrowRight /></button>) : <p>报价、变更和付款条件目前没有冲突，可以继续按计划记录。</p>}</div>
        </article>

        <NextDecision item={dashboard.next_milestone} canWrite={canWrite} uploadCapabilityState={uploadCapabilityState} onAddChange={onAddChange} onUploadEvidence={onUploadEvidence} onBudget={() => onTab("budget")} onAcceptance={onAcceptance} onPayment={() => dashboard.next_milestone ? onPayment() : onTab("payments")} />
      </section>

      <BudgetBridge budget={budget} />
      {(hasChanges || hasTimeline) && <div className="control-secondary-grid" style={!hasChanges || !hasTimeline ? { gridTemplateColumns: "1fr" } : undefined}>
        {hasChanges && <ConfirmationCarousel changes={dashboard.changes} />}
        {hasTimeline && <ProjectStory items={dashboard.timeline} onAll={() => onTab("evidence")} />}
      </div>}
      <section className="control-archive"><div><p>项目结束时，带走完整事实链</p><h3>报价、确认、验收与付款，一份也不少。</h3></div><button onClick={onExport} disabled={!canExport} title={!canExport ? "仅项目所有者可生成完整档案" : undefined}><Download />生成项目档案</button></section>
    </div>
  </section>;
}

function NextDecision({ item, canWrite, uploadCapabilityState, onAddChange, onUploadEvidence, onBudget, onAcceptance, onPayment }: { item: Milestone | null; canWrite: boolean; uploadCapabilityState: UploadCapabilityState; onAddChange: () => void; onUploadEvidence: () => void; onBudget: () => void; onAcceptance: () => void; onPayment: () => void }) {
  const acceptanceReady = !!item && (!requiresAcceptance(item) || !!item.acceptance);
  const paid = !!item && item.paid_cents >= item.planned_amount_cents;
  const uploadsEnabled = uploadCapabilityState === "enabled";
  return <article className="control-decision"><header><div><span>下一次决策</span><h3>{item?.name || "建立付款节点"}</h3></div>{item ? <Money cents={item.planned_amount_cents} /> : <Clock3 />}</header>{item && <p>{formatDate(item.planned_date)} · {item.condition}</p>}<div className="control-quick-actions"><button onClick={onAddChange} disabled={!canWrite}><ReceiptText /><span><strong>记录变化</strong><small>先锁定范围与金额</small></span></button><button onClick={onUploadEvidence} disabled={!canWrite || !uploadsEnabled} title={!uploadsEnabled ? uploadCapabilityMessage(uploadCapabilityState) : !canWrite ? "当前账号只有只读权限" : undefined}><Upload /><span><strong>上传证据</strong><small>{!uploadsEnabled ? "当前环境暂不接收文件" : "保留现场与原件"}</small></span></button><button onClick={onBudget}><FileSpreadsheet /><span><strong>查看预算</strong><small>核对基线与余量</small></span></button><button onClick={acceptanceReady ? onPayment : onAcceptance} disabled={!canWrite || !item || paid}><ClipboardCheck /><span><strong>{!item ? "等待付款节点" : paid ? "该节点已付清" : acceptanceReady ? "付款检查" : "记录验收"}</strong><small>{!item ? "建立节点后再验收" : paid ? "无需再记录付款" : acceptanceReady ? "确认付款条件" : "先完成阶段验收"}</small></span></button></div></article>;
}

function BudgetBridge({ budget }: { budget: Dashboard["budget"] }) {
  const stages = [
    { name: "合同基线", note: budget.baseline_version === null ? "尚未确认" : `版本 V${budget.baseline_version}`, value: budget.baseline_cents },
    { name: "已批准变更", note: "已进入正式预算", value: budget.approved_change_cents },
    { name: "待确认风险", note: "尚未进入正式预算", value: budget.pending_risk_cents },
    { name: "预测结算", note: "当前完整预估", value: budget.predicted_settlement_cents },
  ];
  return <section className="control-bridge" aria-labelledby="budget-path-title"><header className="control-bridge-copy"><p>预算形成路径</p><h3 id="budget-path-title">每个数字从哪里来，一眼能追到。</h3></header><ol className="control-bridge-stages">{stages.map((stage, index) => <li key={stage.name}><span className="control-bridge-index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span><div className="control-bridge-stage-copy"><h4>{stage.name}</h4><p>{stage.note}</p><Money cents={stage.value} /></div></li>)}</ol></section>;
}

function ConfirmationCarousel({ changes }: { changes: Change[] }) {
  const [index,setIndex]=useState(0);
  const item=changes[index % Math.max(changes.length,1)];
  if(!item) return null;
  return <section className="confirmation-carousel"><div className="confirm-portraits"><span>林</span><span>王</span><span>陈</span></div><div className="confirm-quote"><p>“{item.title}”</p><blockquote>{item.status === "approved" ? "范围与金额已经确认，可以纳入当前预算。" : "这项变化仍在等待双方把范围和金额说清楚。"}</blockquote><small>{item.proposer}提出 · {formatDate(item.proposed_on)} · V{item.version}</small></div><div className="carousel-arrows"><button onClick={()=>setIndex((index-1+changes.length)%changes.length)} aria-label="上一条">←</button><button onClick={()=>setIndex((index+1)%changes.length)} aria-label="下一条">→</button></div></section>;
}

function ProjectStory({ items, onAll }: { items: TimelineItem[]; onAll:()=>void }) {
  const scope=useRef<HTMLElement>(null);
  const words="当争议出现时 你不需要凭记忆解释 每一份原件 每一次确认 每一笔付款 都会回到它发生的那一天".split(" ");
  useGSAP(()=>{
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      gsap.set(".story-word, .stack-event", { clearProps: "all" });
      return;
    }
    gsap.utils.toArray<HTMLElement>(".story-word").forEach((word,index)=>gsap.fromTo(word,{opacity:.1},{opacity:1,ease:"none",scrollTrigger:{trigger:scope.current,start:`top+=${index*12} 72%`,end:`top+=${180+index*12} 55%`,scrub:true}}));
    gsap.utils.toArray<HTMLElement>(".stack-event").forEach((card,index)=>gsap.fromTo(card,{y:70,scale:.94},{y:-index*10,scale:1,ease:"none",scrollTrigger:{trigger:card,start:"top 92%",end:"top 48%",scrub:true}}));
  },{scope});
  return <section className="project-story" ref={scope}><div className="story-copy"><p>{words.map((word,index)=><span className="story-word" key={index}>{word} </span>)}</p><button className="text-button" onClick={onAll}>打开完整时间线 →</button></div><div className="story-stack">{items.slice(0,3).map((item,index)=><article className="stack-event" key={item.id} style={{top:`${120+index*34}px`}}><span>{formatDate(item.created_at,true)}</span><h4>{item.title}</h4><p>{item.detail ? formatTimelineDetail(item.detail) : `由${item.actor}记录`}</p>{item.amount_delta_cents!==0&&<Money cents={item.amount_delta_cents} sign />}</article>)}</div></section>;
}

function BudgetView({ dashboard, projectId, canWrite, uploadCapabilityState, canExport, onExport, onOpenTimeline, onChanged, notify }: { dashboard: Dashboard; projectId: string; canWrite: boolean; uploadCapabilityState: UploadCapabilityState; canExport: boolean; onExport: () => void; onOpenTimeline: () => void; onChanged: () => Promise<void>; notify: (message: string, tone?: ToastTone) => void }) {
  const b = dashboard.budget;
  const hasBaseline = b.baseline_version !== null;
  const [drag, setDrag] = useState(false);
  const [quotes, setQuotes] = useState<QuoteSummary[]>([]);
  const [quotesError, setQuotesError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [review, setReview] = useState<QuoteDetail | null>(null);
  const [comparison, setComparison] = useState<QuoteComparison | null>(null);
  const [saving, setSaving] = useState(false);
  const [matching, setMatching] = useState(false);
  const [jobIndex, setJobIndex] = useState(0);
  const [baselineOpen, setBaselineOpen] = useState(false);
  const workbench = useRef<HTMLDivElement>(null);
  const uploadsEnabled = uploadCapabilityState === "enabled";
  const canUpload = canWrite && uploadsEnabled;
  const loadQuotes = useCallback(async () => {
    try {
      const items = await api<QuoteSummary[]>(`/projects/${projectId}/quotes`);
      setQuotes(Array.isArray(items) ? items : []);
      setQuotesError("");
      return true;
    } catch (reason) {
      setQuotesError(errorMessage(reason, "报价列表加载失败"));
      return false;
    }
  }, [projectId]);
  useEffect(() => { const timer = window.setTimeout(() => void loadQuotes(), 0); return () => window.clearTimeout(timer); }, [loadQuotes]);
  const jobQuotes = quotes.filter((quote) => ["queued", "parsing", "parse_failed"].includes(quote.status));
  const hasActiveJobs = quotes.some((quote) => ["queued", "parsing"].includes(quote.status));
  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = window.setInterval(() => { void loadQuotes(); }, 1500);
    return () => window.clearInterval(timer);
  }, [hasActiveJobs, loadQuotes]);
  useGSAP(() => {
    if (!quotes.length) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      gsap.set(".quote-file-card", { clearProps: "all" });
      return;
    }
    gsap.fromTo(".quote-file-card", { y: 18, opacity: 0 }, { y: 0, opacity: 1, duration: .46, stagger: .07, ease: "power2.out" });
    if (window.matchMedia("(min-width: 900px) and (prefers-reduced-motion: no-preference)").matches) {
      ScrollTrigger.create({ trigger: ".quote-workbench", start: "top 108px", end: "bottom 48%", pin: ".quote-workbench-head", pinSpacing: false });
      gsap.utils.toArray<HTMLElement>(".quote-file-card").forEach((card, index) => gsap.fromTo(card, { scale: .985, y: 22 }, { scale: 1, y: 0, ease: "none", scrollTrigger: { trigger: card, start: "top 88%", end: "top 62%", scrub: true }, delay: index * .02 }));
    }
  }, { scope: workbench, dependencies: [quotes.length] });

  async function upload(file?: File) {
    if (!file || uploading) return;
    if (!uploadsEnabled) { notify(uploadCapabilityMessage(uploadCapabilityState), "warning"); return; }
    if (!canWrite) { notify("当前账号只有只读权限，不能导入报价", "error"); return; }
    setUploading(true);
    const data = new FormData();
    data.append("file", file);
    try {
      const response = await fetch(`${API_BASE}/projects/${projectId}/quotes/import`, { method: "POST", body: data, headers: authHeaders(), credentials: "include" });
      const body = await response.json();
      if (!response.ok) throw new Error(body?.error?.message || "报价导入失败");
      const reloaded = await loadQuotes();
      notify(reloaded ? "报价已加入解析队列，可以继续处理其他事项" : "报价已加入解析队列，但列表暂未刷新", reloaded ? "success" : "warning");
    } catch (reason) { notify(errorMessage(reason, "报价导入失败"), "error"); }
    finally { setUploading(false); setDrag(false); }
  }

  async function openReview(id: string) {
    try { setReview(await api<QuoteDetail>(`/quotes/${id}`)); }
    catch (reason) { notify(errorMessage(reason, "报价详情加载失败"), "error"); }
  }

  function editItem(id: string, field: keyof QuoteItemData, value: string | number | null) {
    setReview((current) => current ? { ...current, items: current.items.map((item) => item.id === id ? { ...item, [field]: value } : item) } : current);
  }

  async function saveReview(confirmAfter = false) {
    if (!review || !canWrite) return;
    if (confirmAfter && !window.confirm("确认将当前校对结果标记为正式报价？")) return;
    setSaving(true);
    const results = await Promise.allSettled(review.items.map((item) =>
      api(`/quote-items/${item.id}`, { method: "PATCH", body: JSON.stringify({
          standard_name: item.standard_name, area: item.area, category: item.category,
          quantity: item.quantity, unit: item.unit, unit_price_cents: item.unit_price_cents,
          total_cents: item.total_cents, material_info: item.material_info, craft_notes: item.craft_notes,
        }) })
    ));
    const savedCount = results.filter((result) => result.status === "fulfilled").length;
    if (savedCount !== results.length) {
      notify(`已保存 ${savedCount}/${results.length} 项校对，其余条目保存失败，请核对后重试`, "warning");
      void loadQuotes(); setSaving(false); return;
    }
    try {
      if (confirmAfter) {
        await api(`/quotes/${review.id}/confirm`, { method: "POST" });
        setReview(null);
        const reloaded = await loadQuotes();
        notify(reloaded ? "报价已人工确认，可用于对比或建立基线" : "报价已确认，但列表暂未刷新", reloaded ? "success" : "warning");
      } else {
        try { setReview(await api<QuoteDetail>(`/quotes/${review.id}`)); notify("校对进度已保存，金额已重新计算"); }
        catch { notify("校对进度已保存，但金额摘要暂未刷新", "warning"); }
        void loadQuotes();
      }
    } catch (reason) { notify(confirmAfter ? `校对内容已保存，但报价确认失败：${errorMessage(reason, "请稍后重试")}` : errorMessage(reason, "校对保存失败"), confirmAfter ? "warning" : "error"); }
    finally { setSaving(false); }
  }

  function toggleSelected(id: string) {
    setSelected((current) => current.includes(id) ? current.filter((value) => value !== id) : current.length < 3 ? [...current, id] : current);
    if (!selected.includes(id) && selected.length >= 3) notify("一次最多对比 3 份报价", "warning");
  }

  async function compareSelected() {
    if (selected.length < 2) return notify("请先选择至少 2 份报价");
    try {
      const query = selected.map((id) => `quote_ids=${encodeURIComponent(id)}`).join("&");
      setComparison(await api<QuoteComparison>(`/projects/${projectId}/quotes/compare?${query}`));
    } catch (reason) { notify(errorMessage(reason, "报价对比失败"), "error"); }
  }

  async function refreshComparison() {
    const query = selected.map((id) => `quote_ids=${encodeURIComponent(id)}`).join("&");
    setComparison(await api<QuoteComparison>(`/projects/${projectId}/quotes/compare?${query}`));
  }

  async function confirmMatch(group: ComparisonGroup) {
    if (!canWrite || !window.confirm("确认保留这组人工匹配关系？")) return;
    setMatching(true);
    try {
      await api(`/projects/${projectId}/quote-match-groups`, { method: "POST", body: JSON.stringify({ canonical_name: group.standard_name, item_ids: Object.values(group.items).map((item) => item.id) }) });
      try { await refreshComparison(); notify("项目匹配已人工确认，之后对比会保留这项关系"); }
      catch { notify("匹配关系已保存，但对比结果暂未刷新", "warning"); }
    } catch (reason) { notify(errorMessage(reason, "匹配确认失败"), "error"); }
    finally { setMatching(false); }
  }

  async function removeMatch(group: ComparisonGroup) {
    if (!canWrite || !window.confirm("确认解除这组人工匹配？")) return;
    setMatching(true);
    try {
      await api(`/quote-match-groups/${group.id}`, { method: "DELETE" });
      try { await refreshComparison(); notify("人工匹配已解除，已恢复系统建议"); }
      catch { notify("人工匹配已解除，但对比结果暂未刷新", "warning"); }
    } catch (reason) { notify(errorMessage(reason, "解除匹配失败"), "error"); }
    finally { setMatching(false); }
  }

  async function retryJob(job: QuoteParseJob) {
    if (!canWrite) return;
    try {
      await api(`/quote-jobs/${job.id}/retry`, { method: "POST" });
      const reloaded = await loadQuotes();
      notify(reloaded ? "解析任务已重新排队" : "解析任务已重新排队，但列表暂未刷新", reloaded ? "success" : "warning");
    } catch (reason) { notify(errorMessage(reason, "重试失败"), "error"); }
  }

  async function activate(id: string) {
    if (!canWrite || !window.confirm("设为基线后会创建新版本并影响已批准预算。确认继续？")) return;
    try {
      await api(`/quotes/${id}/activate-baseline`, { method: "POST" });
      const [workspaceResult, quoteResult] = await Promise.allSettled([onChanged(), loadQuotes()]);
      const synced = workspaceResult.status === "fulfilled" && quoteResult.status === "fulfilled" && quoteResult.value;
      notify(synced ? "已创建新的合同基线版本" : "新合同基线已创建，但页面数据暂未完全刷新", synced ? "success" : "warning");
    } catch (reason) { notify(errorMessage(reason, "设为基线失败"), "error"); }
  }

  const focusedJob = jobQuotes.length ? jobQuotes[jobIndex % jobQuotes.length] : undefined;
  return <div className="page-content workspace-page workspace-budget beta-budget" ref={workbench}>
    <section className="page-intro quote-editorial-intro"><div><p className="eyebrow">报价与预算</p><h2>从原始报价，到可信基线</h2><p>解析在后台运行，你可以离开页面；OCR 和匹配只生成草稿，正式金额仍由你校对确认。</p></div><button className="button secondary" onClick={onExport} disabled={!canExport} title={!canExport ? "仅项目所有者可导出" : undefined}><Download size={16} />导出预算档案</button></section>
    {quotesError && <div className="info-banner" role="alert"><AlertTriangle /><div><strong>候选报价暂时无法加载</strong><p>{quotesError}</p><button className="tiny-button" onClick={() => void loadQuotes()}><RefreshCw size={13} />重新加载</button></div></div>}
    <section className="quote-grid">
      <article className="card baseline-card"><div className="section-head"><span className="doc-badge"><BookOpen /></span>{hasBaseline ? <StatusChip status="approved" /> : <span className="status-chip neutral"><span className="status-dot" aria-hidden="true" />尚未建立</span>}</div><p>{hasBaseline ? "当前合同基线" : "合同基线"}</p><h3>{hasBaseline ? `合同预算 V${b.baseline_version}` : "尚未确认合同基线"}</h3><Money cents={b.baseline_cents} /><div className="baseline-meta">{hasBaseline ? <><span><CheckCircle2 /> 已人工确认</span><span>当前有效版本</span></> : <span>从已确认的候选报价设为基线</span>}</div><button className="button secondary full" style={{ display: "inline-flex" }} onClick={() => setBaselineOpen(true)}>{hasBaseline ? "查看基线版本说明" : "查看基线建立说明"}</button></article>
      <label className={cn("quote-upload card", drag && "dragging", (!canUpload || uploading) && "disabled", !uploadsEnabled && "system-disabled")} aria-disabled={!canUpload || uploading} title={!uploadsEnabled ? uploadCapabilityMessage(uploadCapabilityState) : !canWrite ? "当前账号只有只读权限" : undefined} onDragEnter={() => canUpload && !uploading && setDrag(true)} onDragLeave={() => setDrag(false)} onDrop={(event) => { event.preventDefault(); setDrag(false); void upload(event.dataTransfer.files[0]); }} onDragOver={(event) => event.preventDefault()}>
        <input type="file" disabled={!canUpload || uploading} aria-label="导入候选报价文件" aria-describedby="quote-upload-description" accept=".csv,.xlsx,.pdf,.jpg,.jpeg,.png,.heic" onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ""; void upload(file); }} />
        <span className="upload-art">{uploading ? <ScanLine /> : <FileSpreadsheet />}</span><h3>{uploading ? "正在识别结构与金额…" : uploadCapabilityState === "unavailable" ? "当前环境尚未开放候选报价上传" : !uploadsEnabled ? "演示环境已关闭候选报价上传" : canWrite ? "导入一份候选报价" : "只读浏览候选报价"}</h3><p id="quote-upload-description">{!uploadsEnabled ? "候选报价仍可查看和对比；完整部署可启用文件上传。" : canWrite ? "支持表格、文本 PDF、扫描 PDF 与手机图片，单文件最大 30 MB" : "当前账号可以查看、选择和对比报价，但不能上传或修改。"}</p><span className="button primary" aria-hidden="true">{!uploadsEnabled ? "环境限制" : canWrite ? "选择文件" : "只读模式"}</span><small><Sparkles size={13} /> {!uploadsEnabled ? "启用完整部署后可导入新报价" : "识别结果仅作为待校对草稿"}</small>
      </label>
    </section>
    {focusedJob && <section className="parser-command" aria-live="polite">
      <div className="parser-command-summary"><p>解析任务台</p><strong>{hasActiveJobs ? `${jobQuotes.filter((quote) => ["queued", "parsing"].includes(quote.status)).length} 项运行中` : "需要处理"}</strong><span>离开页面不会中断，完成后会自动进入校对。</span></div>
      <div className={cn("parser-job", focusedJob.status === "parse_failed" && "failed")}>
        <div className="parser-job-top"><span>{String((jobIndex % jobQuotes.length) + 1).padStart(2, "0")} / {String(jobQuotes.length).padStart(2, "0")}</span><div className="parser-job-arrows"><button onClick={() => setJobIndex((jobIndex - 1 + jobQuotes.length) % jobQuotes.length)} aria-label="上一个解析任务">←</button><button onClick={() => setJobIndex((jobIndex + 1) % jobQuotes.length)} aria-label="下一个解析任务">→</button></div></div>
        <div className="parser-job-copy"><div><small>{focusedJob.original_name}</small><h3>{focusedJob.parse_job?.stage || "等待任务状态"}</h3><p>{focusedJob.parse_job?.error_message || (focusedJob.status === "parse_failed" ? focusedJob.error_message : `第 ${focusedJob.parse_job?.attempt || 0} 次执行 · 可自动重试 ${focusedJob.parse_job?.max_attempts || 3} 次`)}</p></div><strong>{focusedJob.parse_job?.progress || 0}%</strong></div>
        <div className="parser-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={focusedJob.parse_job?.progress || 0} aria-label={`解析进度 ${focusedJob.parse_job?.progress || 0}%`}><i style={{ width: `${focusedJob.parse_job?.progress || 0}%` }} /></div>
        {focusedJob.status === "parse_failed" && focusedJob.parse_job && <button className="parser-retry" disabled={!canWrite} onClick={() => retryJob(focusedJob.parse_job!)}><RefreshCw size={14} />重新解析</button>}
      </div>
    </section>}
    {quotes.length > 0 && <section className="quote-workbench">
      <div className="quote-workbench-head"><div><p>候选报价</p><h3>先校对，再横向比较</h3></div><button className="button secondary" onClick={compareSelected} disabled={selected.length < 2}><GitCompareArrows size={16} />对比已选 {selected.length || ""}</button></div>
      <div className="imported-quotes">
        {quotes.map((quote) => { const ready = ["reviewing", "confirmed"].includes(quote.status); return <article className={cn("card quote-file-card", selected.includes(quote.id) && "selected", quote.status === "parse_failed" && "failed", !ready && quote.status !== "parse_failed" && "processing")} key={quote.id}>
          <label className="quote-select"><input type="checkbox" aria-label={`选择 ${quote.name} 用于对比`} checked={selected.includes(quote.id)} disabled={!ready} onChange={() => toggleSelected(quote.id)} /><span /></label>
          <div className="quote-file-name">{quote.input_type.includes("pdf") || quote.input_type === "image" ? <ScanLine /> : <FileSpreadsheet />}<span><strong>{quote.name}</strong><small>{ready ? `${quote.item_count} 个条目 · ${quote.parse_method.includes("qwen") ? "通义千问视觉识别" : quote.parse_method === "ocr_tesseract" ? "OCR 识别" : "结构化提取"} · ${quote.status === "confirmed" ? "已确认" : "待校对"}` : quote.parse_job?.stage || "正在准备解析"}</small></span></div>
          <div className="quote-quality"><Money cents={quote.total_cents} />{quote.low_confidence_count > 0 && <small>{quote.low_confidence_count} 项需重点核对</small>}{quote.error_message && <small>{quote.error_message}</small>}</div>
          <div className="quote-card-actions">{ready && <button className="tiny-button" onClick={() => openReview(quote.id)}><Eye size={13} />{canWrite ? "校对" : "查看"}</button>}{quote.status === "confirmed" && <button className="tiny-button approve" disabled={!canWrite} onClick={() => activate(quote.id)}>设为基线</button>}</div>
        </article>; })}
      </div>
    </section>}
    {comparison && <QuoteComparisonPanel data={comparison} busy={matching} canWrite={canWrite} onConfirm={confirmMatch} onRemove={removeMatch} onClose={() => setComparison(null)} />}
    <section className="card ledger-card"><div className="section-head"><div><p className="eyebrow">预算口径</p><h3>金额关系按同一口径计算</h3></div></div><div className="ledger-flow"><div><span>01</span><small>合同基线</small><Money cents={b.baseline_cents} /></div><b>+</b><div><span>02</span><small>已批准变化</small><Money cents={b.approved_change_cents} sign /></div><b>=</b><div className="highlight"><span>03</span><small>已批准预算</small><Money cents={b.approved_budget_cents} /></div><b>+</b><div><span>04</span><small>待审批风险</small><Money cents={b.pending_risk_cents} sign /></div><b>=</b><div className="prediction"><span>05</span><small>预测结算</small><Money cents={b.predicted_settlement_cents} /></div></div><p className="formula-note">待审批风险只是可能发生的金额，不代表已经支出或已经批准。增减项明细可在“增减项”页面核对。</p></section>
    <Modal open={!!review} onClose={() => setReview(null)} eyebrow={review?.parse_method === "ocr_tesseract" ? "OCR 草稿 · 需要人工确认" : "结构化报价 · 需要人工确认"} title={review?.name || "校对候选报价"} className="quote-review-dialog">
      {review && <div className="quote-review-body">
        <div className="review-summary"><div><span>系统重算</span><Money cents={review.items.reduce((sum, item) => sum + item.total_cents, 0)} /></div><div><span>低置信度条目</span><strong>{review.items.filter((item) => item.confidence < 75).length}</strong></div><div><span>已记录修正</span><strong>{review.correction_count}</strong></div><a href={`${API_BASE}/quotes/${review.id}/source`} target="_blank" rel="noreferrer"><Eye size={15} />查看原文件</a></div>
        {review.warnings?.map((warning) => <p className="review-warning" key={warning}><AlertTriangle size={14} />{warning}</p>)}
        <div className="review-table-wrap"><table className="review-table"><thead><tr><th>项目与来源</th><th>空间 / 类别</th><th>数量</th><th>单价</th><th>系统合价</th></tr></thead><tbody>{review.items.map((item) => <tr key={item.id} className={item.confidence < 75 ? "low-confidence" : ""}><td><input disabled={!canWrite} value={item.standard_name} aria-label="标准项目名称" onChange={(event) => editItem(item.id, "standard_name", event.target.value)} /><small>{item.source_location} · 置信度 {item.confidence}%</small><details><summary>查看原文</summary><p>{item.source_excerpt || item.original_name}</p></details></td><td><input disabled={!canWrite} value={item.area || ""} placeholder="空间" aria-label="空间" onChange={(event) => editItem(item.id, "area", event.target.value)} /><input disabled={!canWrite} value={item.category} placeholder="类别" aria-label="类别" onChange={(event) => editItem(item.id, "category", event.target.value)} /></td><td><div className="quantity-edit"><input disabled={!canWrite} value={item.quantity || ""} aria-label="数量" onChange={(event) => editItem(item.id, "quantity", event.target.value)} /><input disabled={!canWrite} value={item.unit || ""} aria-label="单位" onChange={(event) => editItem(item.id, "unit", event.target.value)} /></div></td><td><input disabled={!canWrite} type="number" min="0" step="0.01" value={(item.unit_price_cents || 0) / 100} aria-label="单价" onChange={(event) => editItem(item.id, "unit_price_cents", Math.round(Number(event.target.value) * 100))} /></td><td><Money cents={item.total_cents} /></td></tr>)}</tbody></table></div>
        <p className="review-notice">{canWrite ? "AI 提取可能有误，请以原始文件和人工确认为准。数量和单价变化后，合价由系统重新计算。" : "当前为只读浏览，无法改动或确认报价。AI 提取结果仍应以原始文件和人工确认为准。"}</p>
        <div className="modal-actions"><button className="button ghost" onClick={() => setReview(null)}>{canWrite ? "稍后继续" : "关闭"}</button>{canWrite && <><button className="button secondary" disabled={saving} onClick={() => saveReview(false)}>{saving ? "正在保存…" : "保存校对进度"}</button><button className="button primary" disabled={saving} onClick={() => saveReview(true)}>保存并确认报价</button></>}</div>
      </div>}
    </Modal>
    <Modal open={baselineOpen} onClose={() => setBaselineOpen(false)} eyebrow={hasBaseline ? "当前生效版本" : "尚未建立基线"} title={hasBaseline ? `合同预算 V${b.baseline_version}` : "合同基线尚未确认"}>
      <div className="modal-form"><section className="workspace-metric-strip" aria-label="当前基线摘要"><article><span>合同基线</span><Money cents={b.baseline_cents} /></article><article><span>已批准变化</span><Money cents={b.approved_change_cents} sign /></article><article><span>已批准预算</span><Money cents={b.approved_budget_cents} /></article></section><div className="legal-note"><BookOpen size={17} />{hasBaseline ? "当前接口提供正在生效的版本摘要；每次设为基线都会创建新版本，并在项目时间线永久保留激活记录。" : "先导入并人工确认候选报价，再将它设为合同基线；在此之前系统不会虚构版本号。"}</div><p className="review-notice">计算口径：合同基线 + 已批准增减项 = 已批准预算。候选报价不会在人工确认并设为基线前改变预算。</p><div className="modal-actions"><button className="button ghost" onClick={() => setBaselineOpen(false)}>关闭</button><button className="button primary" onClick={() => { setBaselineOpen(false); onOpenTimeline(); }}><Clock3 size={16} />查看版本时间线</button></div></div>
    </Modal>
  </div>;
}

function QuoteComparisonPanel({ data, busy, canWrite, onConfirm, onRemove, onClose }: { data: QuoteComparison; busy: boolean; canWrite: boolean; onConfirm: (group: ComparisonGroup) => void; onRemove: (group: ComparisonGroup) => void; onClose: () => void }) {
  return <section className="comparison-panel">
    <div className="comparison-head"><div><p>报价横向对比</p><h3>差异回到同一个项目上看</h3><small>{data.notice}</small></div><button className="icon-button" onClick={onClose} aria-label="关闭报价对比"><X size={18} /></button></div>
    <div className="comparison-summary"><article><span>总价区间</span><strong><Money cents={data.summary.lowest_total_cents} /> — <Money cents={data.summary.highest_total_cents} /></strong></article><article><span>总价差</span><Money cents={data.summary.total_spread_cents} /></article><article><span>完整匹配</span><strong>{data.summary.matched_group_count} 组</strong></article></div>
    <div className="comparison-table-wrap"><table className="comparison-table"><thead><tr><th>标准项目</th>{data.quotes.map((quote) => <th key={quote.id}>{quote.name}<small><Money cents={quote.total_cents} /></small></th>)}<th>差额与匹配</th></tr></thead><tbody>{data.groups.map((group) => { const canConfirm = group.match_type === "suggested" && Object.keys(group.items).length >= 2; return <tr key={group.id} className={group.missing_quote_ids.length ? "incomplete" : ""}><td><strong>{group.standard_name}</strong><small>{group.area || "未标空间"} · {group.category} · 匹配 {group.match_confidence}%</small><span className={cn("match-mark", group.match_type === "manual" && "manual")}>{group.match_type === "manual" ? "已人工确认" : "系统建议"}</span></td>{data.quotes.map((quote) => <td key={quote.id}>{group.items[quote.id] ? <><Money cents={group.items[quote.id].total_cents} /><small>{group.items[quote.id].quantity || "—"} {group.items[quote.id].unit || ""}</small></> : <span className="comparison-missing">未报价</span>}</td>)}<td><Money cents={group.price_spread_cents} />{canConfirm && <button className="match-action" disabled={busy || !canWrite} onClick={() => onConfirm(group)}><Link2 size={12} />确认匹配</button>}{group.match_type === "manual" && <button className="match-action remove" disabled={busy || !canWrite} onClick={() => onRemove(group)}><Unlink size={12} />解除匹配</button>}</td></tr>; })}</tbody></table></div>
  </section>;
}

function ChangesView({ changes, busy, canWrite, onAdd, onAction }: { changes: Change[]; busy: boolean; canWrite: boolean; onAdd: () => void; onAction: (id: string, action: string) => void }) {
  const [filter, setFilter] = useState<"all" | "pending" | "approved" | "closed">("all");
  const pending = changes.filter((item) => ["pending_confirmation", "revising"].includes(item.status));
  const approved = changes.filter((item) => ["approved", "implemented", "accepted", "settled"].includes(item.status));
  const closed = changes.filter((item) => !pending.includes(item) && !approved.includes(item));
  const visible = filter === "pending" ? pending : filter === "approved" ? approved : filter === "closed" ? closed : changes;
  const netCents = changes.reduce((total, item) => total + (item.change_type === "increase" ? item.amount_cents : -item.amount_cents), 0);
  const filters = [
    { id: "all" as const, label: "全部", count: changes.length },
    { id: "pending" as const, label: "待处理", count: pending.length },
    { id: "approved" as const, label: "已批准", count: approved.length },
    { id: "closed" as const, label: "其他", count: closed.length },
  ];

  return <div className="page-content workspace-page workspace-changes">
    <section className="page-intro workspace-page-head">
      <div><p className="eyebrow">预算变化</p><h2>先确认，再施工</h2><p>范围、金额和工期放在同一条记录里，每次修订都保留版本。</p></div>
      <button className="button primary" onClick={onAdd} disabled={!canWrite}><Plus size={17} />新建增减项</button>
    </section>
    <section className="workspace-metric-strip" aria-label="增减项摘要">
      <article><span>累计净变化</span><Money cents={netCents} sign /></article>
      <article className={pending.length ? "is-warning" : ""}><span>待确认</span><strong>{pending.length} 项</strong></article>
      <article><span>已批准</span><strong>{approved.length} 项</strong></article>
    </section>
    <nav className="filter-row" aria-label="筛选增减项">
      {filters.map((item) => <button key={item.id} className={cn("filter", filter === item.id && "active")} aria-pressed={filter === item.id} onClick={() => setFilter(item.id)}>{item.label}<span>{item.count}</span></button>)}
    </nav>
    <section className="change-list" aria-live="polite">
      {visible.length ? visible.map((item) => <article className="change-card card" key={item.id}>
        <div className={cn("change-sign", item.change_type === "increase" ? "increase" : "decrease")}>{item.change_type === "increase" ? <ArrowUpRight /> : <ArrowDownRight />}</div>
        <div className="change-main"><div className="change-title"><div><span>{item.area || "合同外项目"} · V{item.version}</span><h3>{item.title}</h3></div><StatusChip status={item.status} /></div><p>{item.content}</p><div className="change-meta"><span><Users />{item.proposer}提出</span><span><CalendarDays />{formatDate(item.proposed_on)}</span>{item.schedule_impact_days !== 0 && <span><Clock3 />工期 {item.schedule_impact_days > 0 ? "+" : ""}{item.schedule_impact_days} 天</span>}</div></div>
        <div className="change-amount"><small>{item.change_type === "increase" ? "增加" : "减少"}</small><Money cents={(item.change_type === "increase" ? 1 : -1) * item.amount_cents} sign />{item.status === "draft" && <div><button className="tiny-button approve" disabled={busy || !canWrite} onClick={() => onAction(item.id, "send")}>发起确认</button></div>}{item.status === "pending_confirmation" && <div><button className="tiny-button approve" disabled={busy || !canWrite} onClick={() => onAction(item.id, "approve")}>内部批准</button><button className="tiny-button" disabled={busy || !canWrite} onClick={() => onAction(item.id, "reject")}>拒绝</button></div>}</div>
      </article>) : <div className="workspace-empty"><ReceiptText /><h3>这个筛选下没有增减项</h3><p>新的现场变化会在记录后出现在这里。</p></div>}
    </section>
  </div>;
}

function PaymentsView({ milestones, payments, paymentsLoading, paymentsError, canWrite, onAdd, onRetry, onAcceptance, onPayment, onReverse }: { milestones: Milestone[]; payments: PaymentRecordData[]; paymentsLoading: boolean; paymentsError: string; canWrite: boolean; onAdd: () => void; onRetry: () => void; onAcceptance: (item: Milestone) => void; onPayment: (item: Milestone) => void; onReverse: (item: PaymentRecordData) => void }) {
  const plannedCents = milestones.reduce((total, item) => total + item.planned_amount_cents, 0);
  const paidCents = milestones.reduce((total, item) => total + item.paid_cents, 0);
  const missingAcceptance = milestones.filter((item) => requiresAcceptance(item) && !item.acceptance).length;
  const reversedIds = new Set(payments.filter((item) => item.record_type === "reversal").map((item) => item.reversal_of_payment_id || item.reference.replace(/^冲正\s*/, "")));
  return <div className="page-content workspace-page workspace-payments">
    <section className="page-intro workspace-page-head"><div><p className="eyebrow">节点账本</p><h2>每笔付款都有前置条件</h2><p>先看验收、再看增减项，最后记录已经发生的付款。</p></div><button className="button primary" onClick={onAdd} disabled={!canWrite}><Plus size={17} />新增付款节点</button></section>
    <section className="workspace-metric-strip" aria-label="付款节点摘要"><article><span>计划总额</span><Money cents={plannedCents} /></article><article><span>已记录付款</span><Money cents={paidCents} /></article><article className={missingAcceptance ? "is-warning" : ""}><span>尚缺验收</span><strong>{missingAcceptance} 个节点</strong></article></section>
    <section className="milestone-list card" aria-label="付款节点列表"><div className="milestone-header"><span>节点</span><span>计划金额</span><span>计划日期</span><span>验收状态</span><span>操作</span></div>{milestones.length ? milestones.map((item, i) => { const complete = item.paid_cents >= item.planned_amount_cents; const acceptanceRequired = requiresAcceptance(item); const progress = item.planned_amount_cents > 0 ? Math.min(100, item.paid_cents / item.planned_amount_cents * 100) : 0; return <article className="milestone-row" key={item.id}>
      <div className="node-name"><span className={cn("node-index", complete && "complete")}>{complete ? <CheckCircle2 /> : i + 1}</span><div><strong>{item.name}</strong><small>{item.condition}</small></div></div>
      <div><Money cents={item.planned_amount_cents} /><small>已记录 <Money cents={item.paid_cents} /></small><span className="milestone-progress" role="progressbar" aria-label={`${item.name}付款进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)}><i style={{ width: `${progress}%` }} /></span></div>
      <div><span>{formatDate(item.planned_date)}</span><small>{new Date(`${item.planned_date}T00:00:00`) <= new Date(`${today()}T00:00:00`) ? "计划日期已到" : "按合同计划"}</small></div>
      <div>{!acceptanceRequired ? <span><CheckCircle2 size={14} />无需验收</span> : item.acceptance ? <details><summary><StatusChip status={item.acceptance.result} /></summary><small>{formatDate(item.acceptance.accepted_on)} · 未关闭问题 {item.acceptance.open_issues} 项{item.acceptance.notes ? ` · ${item.acceptance.notes}` : ""}</small></details> : <span className="missing"><AlertTriangle />尚缺验收</span>}</div>
      <div className="row-buttons"><button className="tiny-button" disabled={!canWrite || !acceptanceRequired || !!item.acceptance} onClick={() => onAcceptance(item)}>{!acceptanceRequired ? "无需验收" : item.acceptance ? "已记录" : "验收"}</button><button className="tiny-button approve" disabled={!canWrite || complete} onClick={() => onPayment(item)}>{complete ? "已付清" : "记录付款"}</button></div>
    </article>; }) : <div className="workspace-empty"><ClipboardCheck /><h3>还没有付款节点</h3><p>先把合同里的阶段金额和付款条件建成节点，之后才能关联验收与流水。</p><button className="button primary" onClick={onAdd} disabled={!canWrite}><Plus size={16} />新增付款节点</button></div>}</section>
    <section className="card timeline-card" aria-label="付款流水"><div className="section-head"><div><p className="eyebrow">付款流水</p><h3>已发生记录与冲正</h3></div><span className="workspace-count">{payments.length} 条</span></div>
      {paymentsError && <div className="info-banner" role="alert"><AlertTriangle /><div><strong>付款流水暂时无法刷新</strong><p>{paymentsError}</p><button className="tiny-button" onClick={onRetry}><RefreshCw size={13} />重新加载</button></div></div>}
      {paymentsLoading && !payments.length ? <div className="inline-loading">正在加载付款流水…</div> : payments.length ? <div className="timeline">{payments.map((item) => { const reversal = item.record_type === "reversal"; const alreadyReversed = reversedIds.has(item.id); return <article key={item.id}><div className="timeline-icon">{reversal ? <RotateCcw size={16} /> : <CircleDollarSign size={16} />}</div><div><div><strong>{reversal ? `冲正：${item.milestone_name}` : item.milestone_name}</strong><time>{formatDate(item.paid_on)}</time></div><p>{item.payee} · {item.method}{item.reference ? ` · ${item.reference}` : ""}{!item.controlled && !reversal ? " · 高风险继续记录" : ""}</p>{item.override_reason && <small>{reversal ? "冲正原因" : "继续记录原因"}：{item.override_reason}</small>}{!reversal && <div className="row-buttons"><button className="tiny-button" disabled={!canWrite || alreadyReversed} onClick={() => onReverse(item)}>{alreadyReversed ? "已冲正" : "冲正"}</button></div>}</div><Money cents={reversal ? -item.amount_cents : item.amount_cents} sign className={reversal ? "money-down" : undefined} /></article>; })}</div> : !paymentsError && <div className="workspace-empty"><CircleDollarSign /><h3>还没有付款流水</h3><p>节点完成付款检查并记录后，会在这里显示收款方、日期、方式和流水号。</p></div>}
    </section>
    <div className="info-banner"><ShieldCheck /><div><strong>“已记录付款”不等于“支付成功”</strong><p>筑账不经手装修款，只连接节点、验收、凭证与已发生事实。</p></div></div>
  </div>;
}

function EvidenceView({ timeline, milestones, evidence, evidenceError, canWrite, uploadCapabilityState, canExport, onRetry, onUpload, onExport }: { timeline: TimelineItem[]; milestones: Milestone[]; evidence: EvidenceItem[]; evidenceError: string; canWrite: boolean; uploadCapabilityState: UploadCapabilityState; canExport: boolean; onRetry: () => void; onUpload: () => void; onExport: () => void }) {
  const decisions = timeline.filter((item) => item.event_type.includes("change") || item.event_type === "acceptance_recorded").length;
  const milestoneNames = new Map(milestones.map((item) => [item.id, item.name]));
  const uploadsEnabled = uploadCapabilityState === "enabled";
  const canUpload = canWrite && uploadsEnabled;
  const uploadTitle = !uploadsEnabled ? uploadCapabilityMessage(uploadCapabilityState) : !canWrite ? "当前账号只有只读权限" : undefined;

  return <div className="page-content workspace-page workspace-evidence">
    <section className="page-intro workspace-page-head">
      <div><p className="eyebrow">项目证据</p><h2>事实按发生顺序保留</h2><p>报价、确认、验收、付款和原始附件连在同一条时间线上。</p></div>
      <div className="button-pair"><button className="button secondary" onClick={onExport} disabled={!canExport} title={!canExport ? "仅项目所有者可导出" : undefined}><FileArchive size={16} />生成档案</button><button className="button primary" onClick={onUpload} disabled={!canUpload} title={uploadTitle}><Upload size={16} />上传证据</button></div>
    </section>
    {!uploadsEnabled && <div className="info-banner upload-disabled-inline" role="status"><ShieldCheck aria-hidden="true" /><div><strong>{uploadCapabilityState === "unavailable" ? "当前环境尚未开放新附件上传" : "演示环境不接收新附件"}</strong><p>已有证据仍可查看和下载；完整部署可启用文件上传。</p></div></div>}
    <section className="workspace-metric-strip" aria-label="项目证据摘要"><article><span>关键事件</span><strong>{timeline.length} 条</strong></article><article><span>原始附件</span><strong>{evidence.length} 份</strong></article><article><span>确认与验收</span><strong>{decisions} 次</strong></article></section>
    <section className="timeline-layout">
      <div className="card timeline-card"><div className="section-head"><div><p className="eyebrow">项目时间线</p><h3>所有关键事件</h3></div><span className="workspace-count">{timeline.length} 条</span></div><Timeline items={timeline} /></div>
      <aside className="evidence-aside">
        <div className="card timeline-card">
          <div className="section-head"><div><p className="eyebrow">原始附件</p><h3>可核对、可下载</h3></div><span className="workspace-count">{evidence.length} 份</span></div>
          {evidenceError && <div className="info-banner" role="alert"><AlertTriangle /><div><strong>附件索引暂时无法加载</strong><p>{evidenceError}</p><button className="tiny-button" onClick={onRetry}><RefreshCw size={13} />重新加载</button></div></div>}
          {evidence.length ? <div className="timeline">{evidence.map((item) => <article key={item.id}><div className="timeline-icon"><Paperclip size={16} /></div><div><div><strong>{item.original_name}</strong><time>{formatDate(item.created_at)}</time></div><p>{item.evidence_type} · {formatBytes(item.size_bytes)}{item.related_type === "milestone" && item.related_id ? ` · 关联 ${milestoneNames.get(item.related_id) || "付款节点"}` : ""}{item.description ? ` · ${item.description}` : ""}</p><a className="tiny-button" href={`${API_BASE}/evidence/${item.id}/download`} target="_blank" rel="noreferrer"><Download size={13} />下载原件</a></div></article>)}</div> : !evidenceError && <div className="workspace-empty"><Paperclip /><h3>{!uploadsEnabled ? uploadCapabilityState === "unavailable" ? "当前环境尚未开放新附件上传" : "演示环境不接收新附件" : "还没有原始附件"}</h3><p>{!uploadsEnabled ? "完整部署可启用上传；当前环境仍保留时间线和档案展示能力。" : canWrite ? "上传现场照片、合同或付款凭证后，可从这里直接下载核对。" : "当前账号可查看现有证据，但没有上传权限。"}</p><button className="button primary" onClick={onUpload} disabled={!canUpload} title={uploadTitle}><Upload size={16} />上传证据</button></div>}
        </div>
        <div className="card archive-promo"><div className="archive-icon"><FileArchive /></div><h3>一份可交接的完整档案</h3><p>汇总 PDF、预算 CSV、时间线与原始附件，需要时可以回到来路。</p><button className="button primary full" onClick={onExport} disabled={!canExport}>生成项目档案</button><small>{canExport ? "报告不构成质量鉴定、价格审定或法律意见" : "仅项目所有者可生成完整档案"}</small></div>
        <div className="card trust-card"><ShieldCheck /><div><strong>原件不被说明覆盖</strong><p>修改描述不会改变已上传的原始文件。</p></div></div>
      </aside>
    </section>
  </div>;
}

function Timeline({ items }: { items: TimelineItem[] }) {
  const icons: Record<string, typeof Plus> = { payment_recorded: CircleDollarSign, baseline_activated: Landmark, change_created: ReceiptText, change_approve: CheckCircle2, acceptance_recorded: ClipboardCheck, evidence_uploaded: Paperclip, project_created: Home };
  if (!items.length) return <div className="workspace-empty"><Clock3 /><h3>时间线还是空的</h3><p>报价、增减项、验收和证据事件会在发生后按时间出现。</p></div>;
  return <div className="timeline project-timeline">{items.map((item) => { const Icon = icons[item.event_type] || Clock3; return <article key={item.id}><div className="timeline-icon" aria-hidden="true"><Icon size={16} /></div><div className="timeline-copy"><strong>{item.title}</strong><p>{item.detail ? formatTimelineDetail(item.detail) : `由${item.actor}记录`}</p></div><div className="timeline-meta"><time dateTime={item.created_at}>{formatDate(item.created_at, true)}</time>{item.amount_delta_cents !== 0 && <Money cents={item.amount_delta_cents} sign className={item.amount_delta_cents > 0 ? "money-up" : "money-down"} />}</div></article>; })}</div>;
}

function PaymentModal({ milestone, onClose, onSubmit, busy }: { milestone: Milestone | null; onClose: () => void; onSubmit: (e: FormEvent<HTMLFormElement>) => void; busy: boolean }) {
  type CheckData = { result: string; checks: { key: string; label: string; ok: boolean; detail: string }[]; planned_remaining_cents: number };
  type CheckState = { status: "idle" } | { status: "loading"; milestoneId: string } | { status: "error"; milestoneId: string; message: string } | { status: "ready"; milestoneId: string; data: CheckData };
  const [checkState, setCheckState] = useState<CheckState>({ status: "idle" });
  const checkRequest = useRef(0);
  const loadCheck = useCallback(async (item: Milestone) => {
    const request = ++checkRequest.current;
    setCheckState({ status: "loading", milestoneId: item.id });
    try {
      const data = await api<CheckData>(`/milestones/${item.id}/payment-check`);
      if (request !== checkRequest.current) return;
      const acceptanceRequired = requiresAcceptance(item);
      const checks = acceptanceRequired ? data.checks : data.checks.map((check) => ["acceptance", "issues"].includes(check.key) ? { ...check, ok: true, detail: "本节点无需验收" } : check);
      const result = acceptanceRequired ? data.result : (checks.some((check) => !check.ok) ? "warning" : "ready");
      setCheckState({ status: "ready", milestoneId: item.id, data: { ...data, checks, result } });
    } catch (reason) {
      if (request === checkRequest.current) setCheckState({ status: "error", milestoneId: item.id, message: errorMessage(reason, "付款检查暂时不可用") });
    }
  }, []);
  useEffect(() => {
    if (!milestone) { checkRequest.current += 1; return; }
    const timer = window.setTimeout(() => void loadCheck(milestone), 0);
    return () => { window.clearTimeout(timer); checkRequest.current += 1; };
  }, [loadCheck, milestone]);
  const state = milestone && "milestoneId" in checkState && checkState.milestoneId === milestone.id ? checkState : { status: "loading" as const, milestoneId: milestone?.id || "" };
  const check = state.status === "ready" ? state.data : null;
  const complete = !!milestone && (milestone.paid_cents >= milestone.planned_amount_cents || !!check && check.planned_remaining_cents <= 0);
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    if (!check || complete) { event.preventDefault(); return; }
    if (check.result === "high_risk" && !window.confirm("付款检查仍有高风险。确认这笔款已经实际发生，并继续保留风险原因？")) { event.preventDefault(); return; }
    onSubmit(event);
  }
  return <Modal open={!!milestone} onClose={busy ? () => undefined : onClose} eyebrow={milestone?.name} title="付款前检查"><form className="modal-form" onSubmit={handleSubmit}>
    {state.status === "loading" && <div className="inline-loading" role="status">正在核对验收、增项和预算…</div>}
    {state.status === "error" && <div className="workspace-empty" role="alert"><AlertTriangle /><h3>付款检查没有完成</h3><p>{state.message}</p><div className="modal-actions"><button type="button" className="button ghost" onClick={onClose}>暂不记录</button><button type="button" className="button primary" onClick={() => milestone && void loadCheck(milestone)}><RefreshCw size={16} />重新检查</button></div></div>}
    {check && complete && <div className="workspace-empty"><CheckCircle2 /><h3>该节点已经付清</h3><p>如需更正金额，请先在付款流水中冲正原记录，再重新付款检查。</p><button type="button" className="button primary" onClick={onClose}>关闭</button></div>}
    {check && !complete && <><div className={cn("check-summary", check.result)}><span>{check.result === "ready" ? <CheckCircle2 /> : <AlertTriangle />}</span><div><strong>{check.result === "ready" ? "付款条件已完成" : check.result === "high_risk" ? "存在高风险，请再次确认" : "存在提醒，可以继续"}</strong><p>以下结果只帮助核对事实，不构成付款建议。</p></div></div><div className="check-list">{check.checks.map(({key,...item}) => <CheckRow key={key} {...item} />)}</div><div className="form-grid"><label className="field"><span>本次实付金额（元）</span><div className="money-input"><b>¥</b><input name="amount" required type="number" min="0.01" max={(check.planned_remaining_cents / 100).toFixed(2)} step="0.01" defaultValue={(check.planned_remaining_cents / 100).toFixed(2)} /></div><small>最多可记录 ¥{(check.planned_remaining_cents / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}</small></label><label className="field"><span>收款方</span><input name="payee" required defaultValue="筑研空间设计工程" /></label><label className="field"><span>付款方式</span><select name="method"><option>银行转账</option><option>微信转账</option><option>支付宝</option><option>现金</option><option>其他</option></select></label><label className="field"><span>流水号或备注</span><input name="reference" placeholder="选填" /></label></div>{check.result === "high_risk" && <label className="field wide danger-field"><span>继续记录原因（必填）</span><textarea name="override_reason" required rows={3} placeholder="说明为什么在风险未关闭时仍记录这笔已发生付款" /></label>}<div className="modal-actions"><button type="button" className="button ghost" disabled={busy} onClick={onClose}>暂不记录</button><button className="button primary" disabled={busy || check.planned_remaining_cents <= 0}>{busy ? "正在记录…" : "确认已发生并记录"}</button></div></>}
  </form></Modal>;
}
