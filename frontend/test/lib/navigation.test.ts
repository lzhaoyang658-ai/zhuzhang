import { describe, expect, it } from "vitest";
import { safeNextPath } from "@/lib/navigation";

describe("safeNextPath", () => {
  it.each([
    ["/", "/"],
    ["/join/invite-token?source=email#accept", "/join/invite-token?source=email#accept"],
    ["/?project=project_123", "/?project=project_123"],
    ["/projects/../join/invite-token", "/join/invite-token"],
  ])("keeps same-origin paths: %s", (raw, expected) => {
    expect(safeNextPath(raw)).toBe(expected);
  });

  it.each([
    null,
    "",
    "projects",
    "https://evil.example/steal",
    "//evil.example/steal",
    "///evil.example/steal",
    "/\\evil.example/steal",
    "\\evil.example/steal",
    "javascript:alert(1)",
  ])("falls back to the project root for unsafe next=%s", (raw) => {
    expect(safeNextPath(raw)).toBe("/");
  });
});
