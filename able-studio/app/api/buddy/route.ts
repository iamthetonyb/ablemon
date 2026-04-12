import { NextResponse } from "next/server";
import { isGatewayConfigured } from "@/lib/control-plane";

const CONTROL_BASE_URL =
  process.env.ABLE_CONTROL_API_BASE ||
  process.env.ABLE_GATEWAY_URL ||
  "";

const EMPTY_BUDDY = {
  buddy: null,
  _status: "gateway_unavailable",
  _message: "ABLE gateway not reachable — set ABLE_CONTROL_API_BASE in env",
};

export async function GET() {
  if (!isGatewayConfigured()) {
    return NextResponse.json({ ...EMPTY_BUDDY, _status: "unconfigured" });
  }

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
      return NextResponse.json(EMPTY_BUDDY);
    }

    const data = await resp.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(EMPTY_BUDDY);
  }
}
