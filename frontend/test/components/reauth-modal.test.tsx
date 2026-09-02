import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReauthDialog } from "@/components/reauth-dialog";
import { Modal } from "@/components/ui";
import { ensureRecentLogin } from "@/lib/reauth";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function ReauthInsideCustomModal() {
  const [result, setResult] = useState("waiting");

  return (
    <>
      <Modal open onClose={() => undefined} title="项目设置">
        <button
          type="button"
          onClick={() => {
            void ensureRecentLogin(new Error("需要重新验证"))
              .then(() => setResult("continued"))
              .catch(() => setResult("cancelled"));
          }}
        >
          保存敏感设置
        </button>
        <output>{result}</output>
      </Modal>
      <ReauthDialog />
    </>
  );
}

describe("global recent-login dialog over a custom Modal", () => {
  it("stays outside the custom modal's inert tree and focus trap", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/session")) {
        return jsonResponse({ user: { email: "OWNER@Example.COM" } });
      }
      if (url.endsWith("/auth/email/request-code")) {
        return jsonResponse({
          challenge_id: "challenge-1",
          expires_in_seconds: 600,
          delivery: "development",
          development_code: "123456",
        });
      }
      if (url.endsWith("/auth/email/reauth")) {
        return new Response(null, { status: 204 });
      }
      return jsonResponse({ error: { message: "unexpected request" } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ReauthInsideCustomModal />);
    fireEvent.click(screen.getByRole("button", { name: "保存敏感设置" }));

    const codeInput = await screen.findByRole("textbox", { name: "6 位验证码" });
    const globalDialog = screen.getByRole("dialog", { name: "重新验证登录邮箱" });

    expect(globalDialog).toHaveAttribute("open");
    expect(globalDialog.closest("[inert]")).toBeNull();
    expect(globalDialog.closest('[aria-hidden="true"]')).toBeNull();
    await waitFor(() => expect(codeInput).toHaveFocus());

    fireEvent.keyDown(codeInput, { key: "Tab" });
    expect(codeInput).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: /123456/ }));
    fireEvent.click(screen.getByRole("button", { name: "验证并继续" }));

    await waitFor(() => expect(screen.getByText("continued")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(screen.getByRole("dialog", { name: "项目设置" })).toBeInTheDocument();
  });
});
