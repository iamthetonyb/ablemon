import { NextResponse } from "next/server";
import { getSetupWizard } from "@/lib/control-plane";

export async function GET() {
  try {
    const data = await getSetupWizard();
    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Failed to load setup wizard" },
      { status: 502 }
    );
  }
}
