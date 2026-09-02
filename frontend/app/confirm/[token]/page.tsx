"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Clock3,
  FileText,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { Money, StatusChip } from "@/components/ui";
import { api } from "@/lib/api";

type ExternalChange = {
  project_name: string;
  read_only: boolean;
  notice: string;
  change: {
    change_type: "increase" | "decrease";
    title: string;
    reason: string;
    content: string;
    amount_cents: number;
    status: string;
    version: number;
    area?: string;
    proposer: string;
    proposed_on: string;
    schedule_impact_days: number;
  };
};

type Decision = "reject" | "request_revision" | "approve";

export default function ConfirmationPage() {
  const { token } = useParams<{ token: string }>();
  const [data, setData] = useState<ExternalChange | null>(null);
  const [loadError, setLoadError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [decision, setDecision] = useState<Decision | "">("");
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const initialLoadRef = useRef<{ token: string; promise: Promise<ExternalChange> } | null>(null);

  async function load() {
    setLoading(true);
    setLoadError("");
    try {
      const result = await api<ExternalChange>(`/external/changes/${token}`);
      setData(result);
    } catch (reason) {
      setLoadError(reason instanceof Error ? reason.message : "暂时无法读取确认记录");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    if (!initialLoadRef.current || initialLoadRef.current.token !== token) {
      initialLoadRef.current = { token, promise: api<ExternalChange>(`/external/changes/${token}`) };
    }
    initialLoadRef.current.promise
      .then((result) => { if (active) setData(result); })
      .catch((reason) => {
        if (active) setLoadError(reason instanceof Error ? reason.message : "暂时无法读取确认记录");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [token]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!data) return;
    if (!decision) {
      setSubmitError("请先明确选择拒绝、要求补充或同意当前版本");
      return;
    }

    const form = new FormData(event.currentTarget);
    setBusy(true);
    setSubmitError("");
    try {
      await api(`/external/changes/${token}/decision`, {
        method: "POST",
        body: JSON.stringify({
          decision,
          name: form.get("name"),
          role: form.get("role"),
          comment: form.get("comment"),
          version: data.change.version,
          current_version_confirmed: form.get("current_version_confirmed") === "on",
        }),
      });
      setDone(true);
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : "提交失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  if (loading && !data) {
    return (
      <main className="workspace-route workspace-confirm-v4 confirm-shell" aria-busy="true">
        <p className="confirm-loading" role="status" aria-live="polite">
          正在核对当前版本…
        </p>
      </main>
    );
  }

  if (loadError && !data) {
    return (
      <main className="workspace-route workspace-confirm-v4 confirm-shell">
        <section className="confirm-error" role="alert">
          <AlertTriangle aria-hidden="true" />
          <h1>无法打开确认记录</h1>
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

  if (done) {
    return (
      <main className="workspace-route workspace-confirm-v4 confirm-shell">
        <section className="confirm-success" role="status">
          <CheckCircle2 aria-hidden="true" />
          <p>{data.project_name}</p>
          <h1>你的意见已被记录</h1>
          <span>该版本已转为只读，原始意见无法由项目成员修改。</span>
          <div><ShieldCheck aria-hidden="true" />普通网页确认不等同于认证电子签名</div>
          <Link className="confirm-success-home" href="/">返回筑账工作台</Link>
        </section>
      </main>
    );
  }

  const item = data.change;
  const signedAmountCents = item.change_type === "decrease" ? -item.amount_cents : item.amount_cents;

  return (
    <main className="workspace-route workspace-confirm-v4 confirm-shell">
      <header className="confirm-brand">
        <span aria-hidden="true">筑</span>
        <div><strong>筑账</strong><small>外部增减项确认</small></div>
      </header>

      <article className="confirm-document">
        <div className="confirm-document-head">
          <div>
            <p>{data.project_name} · 增减项确认</p>
            <h1>{item.title}</h1>
          </div>
          <StatusChip status={item.status} />
        </div>
        <div className="confirm-version">
          <FileText aria-hidden="true" />
          <span>当前版本 V{item.version}</span>
          <span aria-hidden="true">·</span>
          <span>由 {item.proposer} 于 {new Date(item.proposed_on).toLocaleDateString("zh-CN")} 提出</span>
        </div>
        <div className="confirm-amount">
          <span>{item.area || "合同外新项目"} · {item.change_type === "decrease" ? "减少项" : "增加项"}</span>
          <Money cents={signedAmountCents} sign />
          <small>
            {item.schedule_impact_days
              ? `预计工期变化 ${item.schedule_impact_days > 0 ? "+" : ""}${item.schedule_impact_days} 天`
              : "不影响合同工期"}
          </small>
        </div>
        <section><h2>变更原因</h2><p>{item.reason}</p></section>
        <section><h2>施工范围</h2><p>{item.content}</p></section>
        <div className="confirm-note">
          <Clock3 aria-hidden="true" />请确认你查看的是当前版本；版本变化后，本链接会立即失效。
        </div>
      </article>

      {data.read_only ? (
        <section className="confirm-readonly">
          <CheckCircle2 aria-hidden="true" />
          <strong>该版本已完成处理</strong>
          <p>你仍可查看记录，但不能重复提交意见。</p>
        </section>
      ) : (
        <form
          className="decision-form"
          onSubmit={submit}
          aria-describedby={submitError ? "decision-form-error" : undefined}
        >
          <h2>留下你的确认意见</h2>
          <div className="form-grid">
            <label className="field">
              <span>姓名</span>
              <input
                name="name"
                required
                maxLength={80}
                autoComplete="name"
                aria-invalid={submitError ? "true" : undefined}
                aria-describedby={submitError ? "decision-form-error" : undefined}
                placeholder="你的姓名"
              />
            </label>
            <label className="field">
              <span>角色</span>
              <select name="role">
                <option>装修公司项目经理</option>
                <option>工长</option>
                <option>设计师</option>
                <option>监理</option>
                <option>其他</option>
              </select>
            </label>
          </div>
          <label className="field">
            <span>意见</span>
            <textarea name="comment" rows={3} maxLength={2000} placeholder="可补充你确认或拒绝的原因" />
          </label>
          <label className="version-check">
            <input type="checkbox" name="current_version_confirmed" required />
            <span>我确认查看的是当前版本 V{item.version}</span>
          </label>

          <fieldset
            className="decision-fieldset"
            aria-invalid={!decision && submitError ? "true" : undefined}
            aria-describedby={!decision && submitError ? "decision-form-error" : undefined}
          >
            <legend>选择确认意见</legend>
            <div className="decision-buttons">
              <label className={`decision reject${decision === "reject" ? " is-selected" : ""}`}>
                <input
                  type="radio"
                  name="decision"
                  value="reject"
                  checked={decision === "reject"}
                  onChange={() => { setDecision("reject"); setSubmitError(""); }}
                  disabled={busy}
                />
                <XCircle aria-hidden="true" /><span>拒绝</span>
              </label>
              <label className={`decision revise${decision === "request_revision" ? " is-selected" : ""}`}>
                <input
                  type="radio"
                  name="decision"
                  value="request_revision"
                  checked={decision === "request_revision"}
                  onChange={() => { setDecision("request_revision"); setSubmitError(""); }}
                  disabled={busy}
                />
                <AlertTriangle aria-hidden="true" /><span>要求补充</span>
              </label>
              <label className={`decision approve${decision === "approve" ? " is-selected" : ""}`}>
                <input
                  type="radio"
                  name="decision"
                  value="approve"
                  checked={decision === "approve"}
                  onChange={() => { setDecision("approve"); setSubmitError(""); }}
                  disabled={busy}
                />
                <CheckCircle2 aria-hidden="true" /><span>同意当前版本</span>
              </label>
            </div>
          </fieldset>

          {submitError && <p className="form-error" id="decision-form-error" role="alert">{submitError}</p>}

          <button className="decision-submit" type="submit" disabled={busy}>
            <ShieldCheck aria-hidden="true" />
            {busy ? "正在提交…" : decision ? "提交已选意见" : "请先选择确认意见"}
          </button>
          <p className="legal-copy"><ShieldCheck aria-hidden="true" />{data.notice}。本页只记录你提交的事实与意见。</p>
        </form>
      )}
    </main>
  );
}
