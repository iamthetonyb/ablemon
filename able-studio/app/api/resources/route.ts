import { NextResponse } from "next/server";
import { getResources, isGatewayConfigured } from "@/lib/control-plane";

export async function GET() {
  const empty = { resources: [], timestamp: new Date().toISOString(), _status: "gateway_unavailable" };

  if (!isGatewayConfigured()) {
    return NextResponse.json({ ...empty, _status: "unconfigured" });
  }

  try {
    const data = await getResources();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(empty);
  }
}
