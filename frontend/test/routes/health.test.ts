import { describe, expect, it } from "vitest";
import { GET, dynamic } from "@/app/health/route";

describe("GET /health", () => {
  it("returns a minimal, non-cacheable health response", async () => {
    const response = await GET();

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    await expect(response.json()).resolves.toEqual({
      status: "ok",
      service: "frontend",
    });
  });

  it("is always evaluated dynamically", () => {
    expect(dynamic).toBe("force-dynamic");
  });
});
