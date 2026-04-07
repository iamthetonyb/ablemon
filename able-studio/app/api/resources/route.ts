import { NextResponse } from "next/server";
import { getResources } from "@/lib/control-plane";

export async function GET() {
  try {
    const data = await getResources();
    return NextResponse.json(data);
  } catch (error) {
    console.error("Failed to load resources:", error);
    return NextResponse.json(
      { error: "Failed to load resources", resources: [] },
      { status: 502 }
    );
  }
}
