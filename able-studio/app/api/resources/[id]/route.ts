import { NextRequest, NextResponse } from "next/server";
import {
  getResource,
  performResourceAction,
  isGatewayConfigured,
} from "@/lib/control-plane";

type RouteContext = {
  params: Promise<{ id: string }>;
};

export async function GET(_: NextRequest, context: RouteContext) {
  if (!isGatewayConfigured()) {
    return NextResponse.json({ resource: null, _status: "unconfigured" });
  }

  try {
    const { id } = await context.params;
    const payload = await getResource(id);
    return NextResponse.json(payload);
  } catch {
    return NextResponse.json({
      resource: null,
      _status: "gateway_unavailable",
    });
  }
}

export async function POST(req: NextRequest, context: RouteContext) {
  if (!isGatewayConfigured()) {
    return NextResponse.json(
      { error: "Gateway not configured" },
      { status: 503 },
    );
  }

  try {
    const { id } = await context.params;
    const body = await req.json();
    const payload = await performResourceAction(
      id,
      body.action,
      body.approved_by,
    );
    return NextResponse.json(payload);
  } catch {
    return NextResponse.json(
      { error: "Gateway unreachable" },
      { status: 503 },
    );
  }
}
