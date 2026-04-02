import { NextRequest, NextResponse } from "next/server";
import { getResource, performResourceAction } from "@/lib/control-plane";

type RouteContext = {
  params: Promise<{ id: string }>;
};

export async function GET(_: NextRequest, context: RouteContext) {
  try {
    const { id } = await context.params;
    const payload = await getResource(id);
    return NextResponse.json(payload);
  } catch (error) {
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : "Failed to load resource",
      },
      { status: 502 },
    );
  }
}

export async function POST(req: NextRequest, context: RouteContext) {
  try {
    const { id } = await context.params;
    const body = await req.json();
    const payload = await performResourceAction(
      id,
      body.action,
      body.approved_by,
    );
    return NextResponse.json(payload);
  } catch (error) {
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : "Failed to execute resource action",
      },
      { status: 502 },
    );
  }
}
