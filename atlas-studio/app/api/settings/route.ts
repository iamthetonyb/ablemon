import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { featureFlags } from "@/drizzle/schema";
import { eq, isNull, and } from "drizzle-orm";

/**
 * GET /api/settings?org_id=<optional>
 *
 * Returns all feature flags (tool toggles).
 * Used by both the UI and the Python gateway to check which tools are authorized.
 *
 * Python gateway calls this before each agent loop iteration to verify tool access.
 */
export async function GET(req: NextRequest) {
  const orgId = req.nextUrl.searchParams.get("org_id");

  // Get org-specific flags, falling back to global defaults (null org)
  const flags = await db
    .select()
    .from(featureFlags)
    .where(
      orgId
        ? eq(featureFlags.organizationId, orgId)
        : isNull(featureFlags.organizationId)
    );

  // Build a simple map: { tool_name: { enabled, requires_approval, risk_level } }
  const toolMap: Record<string, { enabled: boolean; requires_approval: boolean; risk_level: string }> = {};
  for (const flag of flags) {
    toolMap[flag.toolName] = {
      enabled: flag.enabled,
      requires_approval: flag.requiresApproval,
      risk_level: flag.riskLevel || "medium",
    };
  }

  return NextResponse.json({
    organization_id: orgId || "global",
    tools: toolMap,
    timestamp: new Date().toISOString(),
  });
}

/**
 * PUT /api/settings
 *
 * Update a feature flag toggle.
 * Body: { tool_name, enabled, requires_approval?, org_id? }
 */
export async function PUT(req: NextRequest) {
  const body = await req.json();
  const { tool_name, enabled, requires_approval, org_id } = body;

  if (!tool_name || typeof enabled !== "boolean") {
    return NextResponse.json({ error: "tool_name and enabled required" }, { status: 400 });
  }

  // Update existing flag
  const condition = org_id
    ? and(eq(featureFlags.toolName, tool_name), eq(featureFlags.organizationId, org_id))
    : and(eq(featureFlags.toolName, tool_name), isNull(featureFlags.organizationId));

  const updated = await db
    .update(featureFlags)
    .set({
      enabled,
      ...(requires_approval !== undefined && { requiresApproval: requires_approval }),
      updatedAt: new Date(),
    })
    .where(condition)
    .returning();

  if (updated.length === 0) {
    return NextResponse.json({ error: "Flag not found" }, { status: 404 });
  }

  return NextResponse.json({ success: true, flag: updated[0] });
}
