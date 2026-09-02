import { ensureRecentLogin, recentLoginGeneration } from "@/lib/reauth";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api/v1";

export function authHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const name = "zhuzhang_csrf=";
  const csrf = document.cookie.split(";").map((item) => item.trim()).find((item) => item.startsWith(name))?.slice(name.length);
  return csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {};
}

export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit | undefined, allowRecentLoginRetry: boolean): Promise<T> {
  const generationAtStart = recentLoginGeneration();
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers || {}) },
    cache: "no-store",
    credentials: "include",
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const error = new ApiError(data?.error?.message || "请求失败，请稍后重试", response.status, data?.error?.code);
    if (allowRecentLoginRetry && error.code === "RECENT_LOGIN_REQUIRED") {
      if (generationAtStart === recentLoginGeneration()) await ensureRecentLogin(error);
      return request<T>(path, init, false);
    }
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export function api<T>(path: string, init?: RequestInit): Promise<T> {
  return request<T>(path, init, true);
}

export function apiWithoutRecentLoginRetry<T>(path: string, init?: RequestInit): Promise<T> {
  return request<T>(path, init, false);
}

export function exportJobsUrl(projectId: string) {
  return `${API_BASE}/projects/${projectId}/export-jobs`;
}
