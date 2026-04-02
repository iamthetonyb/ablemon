import { NextResponse } from "next/server";
import { getCollections } from "@/lib/control-plane";

export async function GET() {
  try {
    const payload = await getCollections();
    return NextResponse.json(payload);
  } catch (error) {
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : "Failed to load collections",
      },
      { status: 502 },
    );
  }
}
