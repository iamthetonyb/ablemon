import { NextRequest, NextResponse } from "next/server";
import { isGatewayConfigured } from "@/lib/control-plane";

const CONTROL_BASE_URL =
  process.env.ABLE_CONTROL_API_BASE ||
  process.env.ABLE_GATEWAY_URL ||
  "";

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

  const emptyMetric = { metric, data: {}, _status: "gateway_unavailable" };

  if (!isGatewayConfigured()) {
    return NextResponse.json({ ...emptyMetric, _status: "unconfigured" });
  }

  try {
    const { searchParams } = req.nextUrl;
    const hours = searchParams.get("hours");
    const url = new URL(`${CONTROL_BASE_URL}/metrics/${metric}`);
    if (hours) {
      const h = parseInt(hours, 10);
      if (isNaN(h) || h < 1 || h > 8760) {
        return NextResponse.json({ error: "hours must be 1-8760" }, { status: 400 });
      }
      url.searchParams.set("hours", String(h));
    }

    const headers: Record<string, string> = { Accept: "application/json" };
    const serviceToken = process.env.ABLE_SERVICE_TOKEN;
    if (serviceToken) headers["x-able-service-token"] = serviceToken;

    const resp = await fetch(url.toString(), {
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });

    if (!resp.ok) {
      return NextResponse.json(emptyMetric);
    }

    const data = await resp.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(emptyMetric);
  }
}
