import { NextRequest, NextResponse } from "next/server";

const CONTROL_BASE_URL =
  process.env.ABLE_CONTROL_API_BASE ||
  process.env.ABLE_GATEWAY_URL ||
  "http://127.0.0.1:8080";

const ALLOWED_METRICS = new Set([
  "routing",
  "corpus",
  "evolution",
  "budget",
  "skills",
  "tenants",
  "federation",
]);

type RouteContext = {
  params: Promise<{ metric: string }>;
};

export async function GET(req: NextRequest, context: RouteContext) {
  const { metric } = await context.params;

  if (!ALLOWED_METRICS.has(metric)) {
    return NextResponse.json({ error: `Unknown metric: ${metric}` }, { status: 404 });
  }

  try {
    const { searchParams } = req.nextUrl;
    const hours = searchParams.get("hours");
    const url = new URL(`${CONTROL_BASE_URL}/metrics/${metric}`);
    if (hours) url.searchParams.set("hours", hours);

    const resp = await fetch(url.toString(), {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });

    if (!resp.ok) {
      return NextResponse.json(
        { error: `Gateway returned ${resp.status}` },
        { status: 502 }
      );
    }

    const data = await resp.json();
    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Gateway unreachable" },
      { status: 502 }
    );
  }
}
