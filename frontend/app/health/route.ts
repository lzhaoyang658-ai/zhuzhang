const HEALTH_BODY = {
  status: "ok",
  service: "frontend",
} as const;

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return Response.json(HEALTH_BODY, {
    status: 200,
    headers: {
      "Cache-Control": "no-store",
    },
  });
}
