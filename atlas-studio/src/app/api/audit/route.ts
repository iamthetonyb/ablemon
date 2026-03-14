import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { auditLogs } from "@/db/schema";
import { eq, desc, and } from "drizzle-orm";

export async function GET(req: NextRequest) {
  try {
    const params = req.nextUrl.searchParams;
    const runId = params.get("run_id");
    const agentRole = params.get("agent_role");
    const severity = params.get("severity");
    const orgId = params.get("org_id");
    const limit = parseInt(params.get("limit") || "100");

    const conditions = [];
    if (runId) conditions.push(eq(auditLogs.runId, runId));
    if (agentRole) conditions.push(eq(auditLogs.agentRole, agentRole));
    if (severity) conditions.push(eq(auditLogs.severity, severity));
    if (orgId) conditions.push(eq(auditLogs.organizationId, orgId));

    const logs = await db
      .select()
      .from(auditLogs)
      .where(conditions.length > 0 ? and(...conditions) : undefined)
      .orderBy(desc(auditLogs.createdAt))
      .limit(limit);

    return NextResponse.json({ logs, count: logs.length });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const inserted = await db
      .insert(auditLogs)
      .values({
        runId: body.run_id,
        organizationId: body.org_id || null,
        agentRole: body.agent_role,
        task: body.task,
        content: body.content || null,
        thinkingSteps: body.thinking_steps || null,
        toolCalls: body.tool_calls || null,
        providerUsed: body.provider_used || body.provider || null,
        modelUsed: body.model_used || body.model || null,
        inputTokens: body.input_tokens || 0,
        outputTokens: body.output_tokens || 0,
        costCents: body.cost_cents || 0,
        durationMs: body.duration_ms || 0,
        severity: body.severity || "info",
        status: body.status || "completed",
      })
      .returning();

    return NextResponse.json({ success: true, id: inserted[0]?.id });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
