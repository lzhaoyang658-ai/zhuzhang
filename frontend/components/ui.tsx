"use client";

import { ReactNode, useEffect, useId, useRef } from "react";
import { Check, ChevronRight, X } from "lucide-react";

const focusableSelector = [
  'button:not([disabled])',
  'a[href]',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[contenteditable="true"]',
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function getFocusableElements(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>(focusableSelector)).filter((element) => {
    if (element.closest('[inert], [aria-hidden="true"]')) return false;
    const style = window.getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none" && element.getClientRects().length > 0;
  });
}

function isolateFromAssistiveTechnology(modalRoot: HTMLElement) {
  const changed: Array<{
    element: HTMLElement;
    inert: string | null;
    ariaHidden: string | null;
  }> = [];
  let activeBranch: HTMLElement | null = modalRoot;

  while (activeBranch && activeBranch !== document.body) {
    const parent: HTMLElement | null = activeBranch.parentElement;
    if (!parent) break;

    for (const sibling of Array.from(parent.children)) {
      if (sibling === activeBranch || !(sibling instanceof HTMLElement)) continue;
      // A global security challenge may open above this modal. Keeping that
      // top-layer dialog interactive avoids inheriting this modal's inert state.
      if (sibling.matches("[data-global-modal]")) continue;
      changed.push({
        element: sibling,
        inert: sibling.getAttribute("inert"),
        ariaHidden: sibling.getAttribute("aria-hidden"),
      });
      sibling.setAttribute("inert", "");
      sibling.setAttribute("aria-hidden", "true");
    }

    activeBranch = parent;
  }

  return () => {
    for (const { element, inert, ariaHidden } of changed.reverse()) {
      if (inert === null) element.removeAttribute("inert");
      else element.setAttribute("inert", inert);
      if (ariaHidden === null) element.removeAttribute("aria-hidden");
      else element.setAttribute("aria-hidden", ariaHidden);
    }
  };
}

export function Money({ cents, sign = false, className = "" }: { cents: number; sign?: boolean; className?: string }) {
  const absoluteCents = Math.abs(Math.trunc(cents));
  const wholeYuan = Math.floor(absoluteCents / 100);
  const fraction = absoluteCents % 100;
  const prefix = cents < 0 ? "-" : sign && cents > 0 ? "+" : "";
  const decimal = fraction ? `.${String(fraction).padStart(2, "0")}` : "";
  return <span className={`money ${className}`.trim()}>{prefix}¥{wholeYuan.toLocaleString("zh-CN")}{decimal}</span>;
}

const statusMap: Record<string, string> = {
  draft: "草稿", pending_confirmation: "待确认", revising: "修订中", approved: "已批准",
  rejected: "已拒绝", withdrawn: "已撤回", implemented: "已实施", accepted: "已验收", settled: "已结算",
  passed: "通过", passed_with_issues: "带问题通过", failed: "不通过",
};

export function StatusChip({ status }: { status: string }) {
  const tone = status === "approved" || status === "passed" || status === "settled" ? "good" : status === "pending_confirmation" || status === "revising" || status === "passed_with_issues" ? "wait" : status === "rejected" || status === "failed" ? "bad" : "neutral";
  return <span className={`status-chip ${tone}`}><span className="status-dot" aria-hidden="true" />{statusMap[status] || status}</span>;
}

export function Modal({ open, onClose, title, eyebrow, children, className = "" }: { open: boolean; onClose: () => void; title: string; eyebrow?: string; children: ReactNode; className?: string }) {
  const titleId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);

  useEffect(() => {
    if (!open) return;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const restoreBackground = backdropRef.current
      ? isolateFromAssistiveTechnology(backdropRef.current)
      : () => undefined;
    const focusFirst = window.requestAnimationFrame(() => {
      const dialog = dialogRef.current;
      if (!dialog) return;
      const preferred = dialog.querySelector<HTMLElement>("[autofocus]");
      const first = getFocusableElements(dialog)[0];
      (preferred || first || dialog).focus();
    });
    const handleKeyDown = (event: KeyboardEvent) => {
      if (document.querySelector("[data-global-modal][open]")) return;
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = getFocusableElements(dialogRef.current);
      if (!focusable.length) {
        event.preventDefault();
        dialogRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!dialogRef.current.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    const handleFocusIn = (event: FocusEvent) => {
      if ((event.target as Element | null)?.closest("[data-global-modal][open]")) return;
      const dialog = dialogRef.current;
      if (!dialog || dialog.contains(event.target as Node)) return;
      (getFocusableElements(dialog)[0] || dialog).focus();
    };
    document.addEventListener("keydown", handleKeyDown, true);
    document.addEventListener("focusin", handleFocusIn);
    return () => {
      window.cancelAnimationFrame(focusFirst);
      document.removeEventListener("keydown", handleKeyDown, true);
      document.removeEventListener("focusin", handleFocusIn);
      restoreBackground();
      document.body.style.overflow = previousOverflow;
      const returnTarget = returnFocusRef.current;
      if (returnTarget?.isConnected && !returnTarget.closest("[inert]")) returnTarget.focus();
    };
  }, [open]);

  if (!open) return null;
  return (
    <div ref={backdropRef} className="modal-backdrop" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <section ref={dialogRef} tabIndex={-1} className={`modal-card ${className}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="modal-head">
          <div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h2 id={titleId}>{title}</h2></div>
          <button type="button" className="icon-button" onClick={onClose} aria-label={`关闭“${title}”弹窗`}><X size={20} aria-hidden="true" /></button>
        </div>
        {children}
      </section>
    </div>
  );
}

export function CheckRow({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return <div className="check-row"><span className={`check-icon ${ok ? "ok" : "warn"}`} role="img" aria-label={ok ? "已通过" : "需处理"}>{ok ? <Check size={15} aria-hidden="true" /> : "!"}</span><div><strong>{label}</strong><p>{detail}</p></div><ChevronRight size={16} className="muted" aria-hidden="true" /></div>;
}
