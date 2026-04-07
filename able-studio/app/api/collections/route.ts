import { NextResponse } from "next/server";
import { getCollections } from "@/lib/control-plane";

export async function GET() {
  try {
    const data = await getCollections();
    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Failed to load collections", collections: [] },
      { status: 502 }
    );
  }
}
