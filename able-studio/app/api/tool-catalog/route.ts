import { NextRequest, NextResponse } from "next/server";
import { getToolCatalog } from "@/lib/control-plane";

export async function GET(req: NextRequest) {
  try {
    const orgId = req.nextUrl.searchParams.get("org_id") || undefined;
    const payload = await getToolCatalog(orgId);
    return NextResponse.json(payload);
  } catch (error) {
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : "Failed to load tool catalog",
      },
      { status: 502 },
    );
  }
}
