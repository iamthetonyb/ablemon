import { NextResponse } from "next/server";
import { getResources } from "@/lib/control-plane";

export async function GET() {
  try {
    const payload = await getResources();
    return NextResponse.json(payload);
  } catch (error) {
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : "Failed to load resources",
      },
      { status: 502 },
    );
  }
}
