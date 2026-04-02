import { NextResponse } from "next/server";
import { getSetupWizard } from "@/lib/control-plane";

export async function GET() {
  try {
    const payload = await getSetupWizard();
    return NextResponse.json(payload);
  } catch (error) {
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : "Failed to load setup wizard",
      },
      { status: 502 },
    );
  }
}
