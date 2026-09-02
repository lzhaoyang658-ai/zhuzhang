"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { KeyRound, LoaderCircle, Mail, ShieldCheck, X } from "lucide-react";
import { ApiError, apiWithoutRecentLoginRetry } from "@/lib/api";
import { registerRecentLoginHandler } from "@/lib/reauth";

type SessionResult = { user: { email: string } };
type CodeResult = {
  challenge_id: string;
  expires_in_seconds: number;
  delivery: string;
  development_code?: string;
};
type Phase = "sending" | "code" | "verifying" | "prepare-error";
type PendingAttempt = {
  id: number;
  promise: Promise<void>;
  resolve: () => void;
  reject: (reason: Error) => void;
  controller: AbortController;
};

function messageFrom(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

export function ReauthDialog() {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const codeInputRef = useRef<HTMLInputElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const pendingRef = useRef<PendingAttempt | null>(null);
  const nextAttemptIdRef = useRef(0);
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>("sending");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [developmentCode, setDevelopmentCode] = useState("");
  const [error, setError] = useState("");

  const prepare = useCallback(async (attemptId: number, controller: AbortController) => {
    setPhase("sending");
    setError("");
    setCode("");
    setDevelopmentCode("");
    try {
      const session = await apiWithoutRecentLoginRetry<SessionResult>("/session", { signal: controller.signal });
      const currentEmail = session.user.email.trim().toLowerCase();
      if (!currentEmail) throw new ApiError("当前会话没有可验证的邮箱", 400, "SESSION_EMAIL_MISSING");
      if (pendingRef.current?.id !== attemptId) return;
      setEmail(currentEmail);
      const result = await apiWithoutRecentLoginRetry<CodeResult>("/auth/email/request-code", {
        method: "POST",
        body: JSON.stringify({ email: currentEmail }),
        signal: controller.signal,
      });
      if (pendingRef.current?.id !== attemptId) return;
      setDevelopmentCode(result.development_code || "");
      setPhase("code");
    } catch (reason) {
      if (controller.signal.aborted || pendingRef.current?.id !== attemptId) return;
      setError(messageFrom(reason, "验证码暂时无法发送，请稍后重试"));
      setPhase("prepare-error");
    }
  }, []);

  const begin = useCallback(() => {
    if (pendingRef.current) return pendingRef.current.promise;

    const id = ++nextAttemptIdRef.current;
    const controller = new AbortController();
    let resolveAttempt!: () => void;
    let rejectAttempt!: (reason: Error) => void;
    const promise = new Promise<void>((resolve, reject) => {
      resolveAttempt = resolve;
      rejectAttempt = reject;
    });
    pendingRef.current = { id, promise, resolve: resolveAttempt, reject: rejectAttempt, controller };
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setEmail("");
    setOpen(true);
    void prepare(id, controller);
    return promise;
  }, [prepare]);

  const cancel = useCallback((message = "已取消邮箱重新验证，原操作未执行") => {
    const pending = pendingRef.current;
    if (!pending) return;
    pendingRef.current = null;
    pending.controller.abort();
    pending.reject(new ApiError(message, 401, "RECENT_LOGIN_CANCELLED"));
    setOpen(false);
  }, []);

  useEffect(() => {
    const unregister = registerRecentLoginHandler(begin);
    return () => {
      unregister();
      const pending = pendingRef.current;
      if (pending) {
        pendingRef.current = null;
        pending.controller.abort();
        pending.reject(new ApiError("重新验证界面已关闭，原操作未执行", 401, "RECENT_LOGIN_UNAVAILABLE"));
      }
    };
  }, [begin]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) {
      dialog.close();
      window.requestAnimationFrame(() => {
        if (openerRef.current?.isConnected) openerRef.current.focus();
      });
    }
  }, [open]);

  useEffect(() => {
    if (open && phase === "code") codeInputRef.current?.focus();
  }, [open, phase]);

  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const pending = pendingRef.current;
    if (!pending || code.length !== 6) return;
    setPhase("verifying");
    setError("");
    try {
      await apiWithoutRecentLoginRetry("/auth/email/reauth", {
        method: "POST",
        body: JSON.stringify({ email, code }),
        signal: pending.controller.signal,
      });
      if (pendingRef.current?.id !== pending.id) return;
      pendingRef.current = null;
      setOpen(false);
      pending.resolve();
    } catch (reason) {
      if (pending.controller.signal.aborted || pendingRef.current?.id !== pending.id) return;
      setError(messageFrom(reason, "验证失败，请检查验证码后重试"));
      setPhase("code");
    }
  }

  const busy = phase === "sending" || phase === "verifying";
  const descriptionIds = `reauth-description${error ? " reauth-error" : ""}`;

  return (
    <dialog
      ref={dialogRef}
      className="reauth-dialog"
      data-global-modal
      aria-labelledby="reauth-title"
      aria-describedby={descriptionIds}
      onCancel={(event) => {
        event.preventDefault();
        cancel();
      }}
    >
      <div className="reauth-dialog-head">
        <span aria-hidden="true"><ShieldCheck /></span>
        <div>
          <p>敏感操作保护</p>
          <h2 id="reauth-title">重新验证登录邮箱</h2>
        </div>
        <button type="button" onClick={() => cancel()} aria-label="取消重新验证">
          <X aria-hidden="true" />
        </button>
      </div>

      <p id="reauth-description" className="reauth-dialog-description">
        为保护项目和资金记录，请输入发送到当前登录邮箱的一次性验证码。验证成功后，刚才的操作会自动继续一次。
      </p>

      {email && (
        <div className="reauth-dialog-email">
          <Mail aria-hidden="true" />
          <span>验证码发送至</span>
          <strong>{email}</strong>
        </div>
      )}

      {phase === "sending" && (
        <p className="reauth-dialog-status" role="status" aria-live="polite">
          <LoaderCircle className="reauth-spin" aria-hidden="true" />正在获取当前会话并发送验证码…
        </p>
      )}

      {phase === "prepare-error" && (
        <div className="reauth-dialog-recovery">
          {error && <p id="reauth-error" role="alert">{error}</p>}
          <button
            type="button"
            onClick={() => {
              const pending = pendingRef.current;
              if (pending) void prepare(pending.id, pending.controller);
            }}
          >
            重新获取验证码
          </button>
        </div>
      )}

      {(phase === "code" || phase === "verifying") && (
        <form onSubmit={verify} aria-describedby={descriptionIds}>
          <label>
            <span>6 位验证码</span>
            <div className="reauth-code-field">
              <KeyRound aria-hidden="true" />
              <input
                ref={codeInputRef}
                value={code}
                onChange={(event) => {
                  setCode(event.target.value.replace(/\D/g, "").slice(0, 6));
                  setError("");
                }}
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="\d{6}"
                required
                aria-invalid={error ? "true" : undefined}
                aria-describedby={error ? "reauth-error" : undefined}
                disabled={phase === "verifying"}
              />
            </div>
          </label>

          {developmentCode && (
            <button type="button" className="reauth-development-code" onClick={() => setCode(developmentCode)}>
              开发环境验证码 <strong>{developmentCode}</strong><span>点击填入</span>
            </button>
          )}
          {error && <p id="reauth-error" className="reauth-dialog-error" role="alert">{error}</p>}

          <div className="reauth-dialog-actions">
            <button type="button" className="secondary" onClick={() => cancel()} disabled={phase === "verifying"}>取消</button>
            <button type="submit" disabled={phase === "verifying" || code.length !== 6}>
              {phase === "verifying" ? <><LoaderCircle className="reauth-spin" aria-hidden="true" />正在验证</> : "验证并继续"}
            </button>
          </div>
        </form>
      )}

      {busy && phase !== "verifying" && (
        <button type="button" className="reauth-dialog-cancel" onClick={() => cancel()}>取消并返回</button>
      )}
    </dialog>
  );
}
