/**
 * Limit post-login navigation to a same-origin absolute path.
 *
 * Protocol-relative URLs and backslashes are rejected before URL parsing
 * because browsers can interpret both as an authority separator.
 */
export function safeNextPath(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return "/";

  try {
    const base = "https://zhuzhang.local";
    const parsed = new URL(raw, base);
    if (parsed.origin !== base) return "/";
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return "/";
  }
}
