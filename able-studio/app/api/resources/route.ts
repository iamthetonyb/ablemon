import { NextResponse } from "next/server";
import { getResources } from "@/lib/control-plane";

export async function GET() {
  try {
    const data = await getResources();
    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Failed to load resources", resources: [] },
      { status: 502 }
    );
  }
}
