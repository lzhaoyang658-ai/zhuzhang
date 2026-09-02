"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ArrowRight, Check, FileCheck2, LockKeyhole, Mail, ReceiptText, ShieldCheck, WalletCards } from "lucide-react";
import { ApiError, api } from "@/lib/api";
import { safeNextPath } from "@/lib/navigation";

type CodeResult = { challenge_id: string; expires_in_seconds: number; delivery: string; development_code?: string };

function nextPathFromLocation() {
  if (typeof window === "undefined") return "/";
  return safeNextPath(new URLSearchParams(window.location.search).get("next"));
}

export default function LoginPage() {
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [developmentCode, setDevelopmentCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const scope = useRef<HTMLElement>(null);

  useEffect(() => {
    api<{ authenticated: boolean }>("/auth/status").then((result) => { if (result.authenticated) window.location.replace(nextPathFromLocation()); }).catch(() => undefined);
  }, []);

  useGSAP(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      gsap.set(".login-v2-copy > *, .login-v2-card", { clearProps: "all" });
      return;
    }
    gsap.fromTo(".login-v2-copy > *", { y: 22, opacity: 0 }, { y: 0, opacity: 1, duration: .62, stagger: .07, ease: "power3.out" });
    gsap.fromTo(".login-v2-card", { y: 30, scale: .97, opacity: 0 }, { y: 0, scale: 1, opacity: 1, duration: .78, delay: .15, ease: "power3.out" });
  }, { scope });

  async function requestCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await api<CodeResult>("/auth/email/request-code", { method: "POST", body: JSON.stringify({ email }) });
      setDevelopmentCode(result.development_code || "");
      if (result.development_code) setCode(result.development_code);
      setStep("code");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "验证码发送失败"); }
    finally { setBusy(false); }
  }

  async function verifyCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await api("/auth/email/verify", { method: "POST", body: JSON.stringify({ email, code }) });
      window.location.replace(nextPathFromLocation());
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === "AUTH_CODE_EXPIRED") setStep("email");
      setError(reason instanceof Error ? reason.message : "登录失败");
    } finally { setBusy(false); }
  }

  return <main className="login-v2 overflow-x-hidden w-full max-w-full" ref={scope}>
    <nav className="login-v2-nav"><Link href="/login"><span>筑</span><strong>筑账</strong></Link><div><ShieldCheck />业主自己的装修事实账本</div></nav>
    <section className="login-v2-stage">
      <div className="login-v2-copy">
        <p className="login-v2-kicker">装修预算与增项管家</p>
        <h1><span>先把每一笔钱说明白，</span><span>再继续施工。</span></h1>
        <p className="login-v2-lead">报价、增减项、验收、付款和现场证据放在同一条记录链上。每次付款前，先确认范围、金额与依据。</p>
        <div className="login-v2-proof" aria-label="产品核心能力">
          <article><WalletCards /><strong>预算始终有边界</strong><span>合同、已批准变更和待确认风险分开计算</span></article>
          <article><ReceiptText /><strong>变更先确认再施工</strong><span>范围、金额、工期与提出人全部留痕</span></article>
          <article><FileCheck2 /><strong>付款前核对证据</strong><span>验收结果、凭证和付款节点彼此关联</span></article>
        </div>
      </div>
      <article className="login-v2-card" id="login">
        <header><span>进入你的项目</span><h2>邮箱验证码登录</h2><p>无需设置密码，验证码只发送到你的邮箱。</p></header>
        <div className="login-v2-form-icon"><Mail /></div>
        {step === "email" ? <form onSubmit={requestCode}><label><span>登录邮箱</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoFocus autoComplete="email" aria-invalid={!!error} placeholder="你的 QQ 邮箱" /></label>{error && <p className="login-form-error" role="alert">{error}</p>}<button className="login-v2-submit" disabled={busy}>{busy ? "正在发送验证码" : "获取验证码"}<ArrowRight /></button></form> : <form onSubmit={verifyCode}><div className="login-code-sent" role="status"><Check />验证码已发送至 <strong>{email}</strong></div><label><span>6 位验证码</span><input value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric" autoFocus autoComplete="one-time-code" aria-invalid={!!error} required pattern="\d{6}" /></label>{developmentCode && <button type="button" className="development-code" onClick={() => setCode(developmentCode)}>本地测试码 <strong>{developmentCode}</strong></button>}{error && <p className="login-form-error" role="alert">{error}</p>}<button className="login-v2-submit" disabled={busy || code.length !== 6}>{busy ? "正在验证" : "验证并进入项目"}<ArrowRight /></button><button type="button" className="login-back" onClick={() => { setStep("email"); setCode(""); setError(""); }}>更换邮箱</button></form>}
        <p className="login-v2-privacy"><LockKeyhole />本设备会建立独立安全会话，可随时在账号设置中撤销。</p>
      </article>
    </section>
    <footer className="login-v2-footer"><span>原文件可回溯</span><span>重要记录保留版本</span><span>项目数据支持完整导出</span></footer>
  </main>;
}
