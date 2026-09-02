"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  LogIn,
  LogOut,
  RefreshCw,
  ShieldCheck,
  Users,
} from "lucide-react";
import { ApiError, api } from "@/lib/api";

type InviteInfo = {
  project_name: string;
  email: string;
  role: "co_manager" | "viewer";
  expires_at: string;
};

type AcceptedInfo = {
  user?: { id: string; name: string; email: string };
  project_id: string;
  role: string;
};

type SessionInfo = { user: { id: string; name: string; email: string } };
type JoinContext = { invite: InviteInfo; session: SessionInfo | null };

const roleNames = { co_manager: "共同管理者", viewer: "只读成员" };

export default function JoinProjectPage() {
  const { token } = useParams<{ token: string }>();
  const [data, setData] = useState<InviteInfo | null>(null);
  const [accepted, setAccepted] = useState<AcceptedInfo | null>(null);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [accountMismatch, setAccountMismatch] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const scope = useRef<HTMLElement>(null);
  const initialLoadRef = useRef<{ token: string; promise: Promise<JoinContext> } | null>(null);

  const fetchContext = useCallback(async (): Promise<JoinContext> => {
    const [invite, status] = await Promise.all([
      api<InviteInfo>(`/invites/${token}`),
      api<{ authenticated: boolean }>("/auth/status"),
    ]);
    if (!status.authenticated) return { invite, session: null };
    try {
      return { invite, session: await api<SessionInfo>("/session") };
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === "AUTH_REQUIRED") return { invite, session: null };
      throw reason;
    }
  }, [token]);

  const applyContext = useCallback((context: JoinContext) => {
    setData(context.invite);
    setSession(context.session);
    setAccountMismatch(false);
  }, []);

  async function load() {
    setLoading(true);
    setLoadError("");
    try {
      applyContext(await fetchContext());
    } catch (reason) {
      setLoadError(reason instanceof Error ? reason.message : "暂时无法核对邀请状态");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    if (!initialLoadRef.current || initialLoadRef.current.token !== token) {
      initialLoadRef.current = { token, promise: fetchContext() };
    }
    initialLoadRef.current.promise
      .then((result) => { if (active) applyContext(result); })
      .catch((reason) => {
        if (active) setLoadError(reason instanceof Error ? reason.message : "暂时无法核对邀请状态");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [token, fetchContext, applyContext]);

  useGSAP(() => {
    if (!data) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      gsap.set(".join-avatar, .join-word", { clearProps: "all" });
      return;
    }
    gsap.fromTo(
      ".join-avatar",
      { scale: 0.8, opacity: 0 },
      { scale: 1, opacity: 1, duration: 0.8, stagger: 0.08, ease: "power3.out" },
    );
    gsap.fromTo(
      ".join-word",
      { y: 10 },
      { y: 0, duration: 0.55, stagger: 0.045, ease: "power2.out" },
    );
  }, { scope, dependencies: [data?.project_name] });

  async function accept(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session) return;
    setBusy(true);
    setSubmitError("");
    setAccountMismatch(false);
    try {
      const result = await api<AcceptedInfo>(`/invites/${token}/accept`, {
        method: "POST",
        body: JSON.stringify({ name: session.user.name }),
      });
      setAccepted(result);
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === "INVITE_EMAIL_MISMATCH") setAccountMismatch(true);
      if (reason instanceof ApiError && reason.code === "AUTH_REQUIRED") setSession(null);
      setSubmitError(reason instanceof Error ? reason.message : "加入项目失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  async function switchAccount() {
    setBusy(true);
    setSubmitError("");
    try {
      await api("/auth/logout", { method: "POST" });
      window.location.replace(loginHref);
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === "AUTH_REQUIRED") {
        window.location.replace(loginHref);
        return;
      }
      setSubmitError(reason instanceof Error ? reason.message : "暂时无法退出当前账号，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  if (loading && !data) {
    return (
      <main className="workspace-route workspace-join-v4 join-shell" aria-busy="true">
        <p className="join-loading" role="status" aria-live="polite">正在核对邀请状态…</p>
      </main>
    );
  }

  if (loadError && !data) {
    return (
      <main className="workspace-route workspace-join-v4 join-shell">
        <section className="join-error" role="alert">
          <AlertTriangle aria-hidden="true" />
          <h1>无法使用这份邀请</h1>
          <p>{loadError}</p>
          <div className="route-state-actions">
            <button type="button" onClick={() => void load()} disabled={loading}>
              <RefreshCw aria-hidden="true" />
              {loading ? "正在重试…" : "重新加载"}
            </button>
            <Link href="/">
              <ArrowLeft aria-hidden="true" />返回筑账
            </Link>
          </div>
        </section>
      </main>
    );
  }

  if (!data) return null;

  const returnPath = `/join/${encodeURIComponent(token)}`;
  const loginHref = `/login?next=${encodeURIComponent(returnPath)}`;

  if (accepted) {
    return (
      <main className="workspace-route workspace-join-v4 join-shell">
        <section className="join-success" role="status">
          <CheckCircle2 aria-hidden="true" />
          <p>{data.project_name}</p>
          <h1>你已加入这个装修项目</h1>
          <span>当前身份：{roleNames[data.role]}</span>
          <Link href={`/?project=${encodeURIComponent(accepted.project_id)}`}>进入项目 <ArrowRight aria-hidden="true" /></Link>
        </section>
      </main>
    );
  }

  const words = "家里的每一笔变化 都让重要的人看见".split(" ");

  return (
    <main className="workspace-route workspace-join-v4 join-shell" ref={scope}>
      <nav className="join-nav" aria-label="筑账">
        <Link href="/"><span aria-hidden="true">筑</span><strong>筑账</strong></Link>
        <small>家庭协作邀请</small>
      </nav>
      <section className="join-hero">
        <div className="join-copy">
          <p>{data.project_name}</p>
          <h1>{words.map((word) => <span className="join-word" key={word}>{word} </span>)}</h1>
          <div className="join-avatars" aria-hidden="true">
            <span className="join-avatar">林</span>
            <span className="join-avatar">家</span>
            <span className="join-avatar"><Users /></span>
          </div>
        </div>
        <aside className="join-card">
          <p>邀请发送至</p>
          <strong>{data.email}</strong>
          <div className="join-role">
            <ShieldCheck aria-hidden="true" />
            <span><small>项目角色</small>{roleNames[data.role]}</span>
          </div>
          <p className="join-permission">
            {data.role === "co_manager"
              ? "可查看并编辑业务数据、确认增减项；不能管理成员或删除项目。"
              : "可查看完整项目数据；不能修改、确认或管理项目。"}
          </p>
          {!session ? (
            <section className="join-auth-gate" aria-labelledby="join-auth-title">
              <LogIn aria-hidden="true" />
              <div>
                <strong id="join-auth-title">请先登录受邀邮箱</strong>
                <p>邀请链接只证明你获准加入项目；还需通过收件邮箱验证码确认账号身份。</p>
              </div>
              <Link href={loginHref}>邮箱验证码登录 <ArrowRight aria-hidden="true" /></Link>
            </section>
          ) : (
            <>
              <div className={`join-current-account${accountMismatch ? " mismatch" : ""}`}>
                <span>当前登录邮箱</span>
                <strong>{session.user.email}</strong>
                <button type="button" onClick={() => void switchAccount()} disabled={busy}>
                  <LogOut aria-hidden="true" />切换账号
                </button>
              </div>
              {accountMismatch && (
                <div className="join-account-mismatch" role="alert">
                  <AlertTriangle aria-hidden="true" />
                  <p><strong>当前账号不是受邀邮箱</strong><span>请退出后，使用收到这份邀请的邮箱重新登录。</span></p>
                </div>
              )}
              <form
                onSubmit={accept}
                aria-describedby={submitError ? "join-form-error" : undefined}
              >
                {submitError && <p className="form-error" id="join-form-error" role="alert">{submitError}</p>}
                <button className="join-action" disabled={busy || accountMismatch}>
                  {busy ? "正在处理…" : accountMismatch ? "请先切换至受邀邮箱" : "接受邀请并进入项目"}<ArrowRight aria-hidden="true" />
                </button>
              </form>
            </>
          )}
          <small className="join-expiry">邀请有效期至 {new Date(data.expires_at).toLocaleDateString("zh-CN")}</small>
        </aside>
      </section>
    </main>
  );
}
