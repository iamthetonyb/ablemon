import { NextResponse } from "next/server";
import { getCollections, isGatewayConfigured } from "@/lib/control-plane";

export async function GET() {
  const empty = { collections: [], timestamp: new Date().toISOString(), _status: "gateway_unavailable" };

  if (!isGatewayConfigured()) {
    return NextResponse.json({ ...empty, _status: "unconfigured" });
  }

  try {
    const data = await getCollections();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(empty);
  }
}
