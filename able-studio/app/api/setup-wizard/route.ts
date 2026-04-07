import { NextResponse } from "next/server";
import { getSetupWizard } from "@/lib/control-plane";

export async function GET() {
  try {
    const data = await getSetupWizard();
    return NextResponse.json(data);
  } catch (error) {
    console.error("Failed to load setup wizard:", error);
    return NextResponse.json(
      { error: "Failed to load setup wizard" },
      { status: 502 }
    );
  }
}
