import { NextResponse } from "next/server";
import { getSetupWizard, isGatewayConfigured } from "@/lib/control-plane";

const EMPTY_WIZARD = {
  steps: [],
  completed: false,
  _status: "gateway_unavailable",
};

export async function GET() {
  if (!isGatewayConfigured()) {
    return NextResponse.json({ ...EMPTY_WIZARD, _status: "unconfigured" });
  }

  try {
    const data = await getSetupWizard();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(EMPTY_WIZARD);
  }
}
