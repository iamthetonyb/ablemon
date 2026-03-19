import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { auditLogs } from "@/drizzle/schema";
import { eq, desc, and, gte, sql } from "drizzle-orm";

/**
 * GET /api/audit?run_id=<optional>&agent_role=<optional>&severity=<optional>&limit=<optional>&org_id=<optional>
 *
 * Returns audit log entries with filters.
 */
export async function GET(req: NextRequest) {
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
}

/**
 * POST /api/audit
 *
 * Insert a new audit log entry (called by Python gateway).
 * Body: { run_id, agent_role, task, content, thinking_steps?, tool_calls?, ... }
 */
export async function POST(req: NextRequest) {
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
      providerUsed: body.provider || null,
      modelUsed: body.model || null,
      inputTokens: body.input_tokens || 0,
      outputTokens: body.output_tokens || 0,
      costCents: body.cost_cents || 0,
      durationMs: body.duration_ms || 0,
      severity: body.severity || "info",
      status: body.status || "completed",
    })
    .returning();

  return NextResponse.json({ success: true, id: inserted[0]?.id });
}
