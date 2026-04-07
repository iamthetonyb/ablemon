import { NextResponse } from "next/server";

const CONTROL_BASE_URL =
  process.env.ABLE_CONTROL_API_BASE ||
  process.env.ABLE_GATEWAY_URL ||
  "http://127.0.0.1:8080";

export async function GET() {
  try {
    const headers: Record<string, string> = { Accept: "application/json" };
    const serviceToken = process.env.ABLE_SERVICE_TOKEN;
    if (serviceToken) headers["x-able-service-token"] = serviceToken;

    const resp = await fetch(`${CONTROL_BASE_URL}/api/buddy`, {
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });

    if (!resp.ok) {
      return NextResponse.json(
        { buddy: null, error: `Gateway returned ${resp.status}` },
        { status: 502 }
      );
    }

    const data = await resp.json();
    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json(
      { buddy: null, error: error instanceof Error ? error.message : "Gateway unreachable" },
      { status: 502 }
    );
  }
}
