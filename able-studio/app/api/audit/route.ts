import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { auditLogs } from "@/drizzle/schema";
import { desc, eq, and, gte, lte, sql } from "drizzle-orm";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = req.nextUrl;
    const limit = Math.min(parseInt(searchParams.get("limit") ?? "50", 10), 200);
    const severity = searchParams.get("severity");
    const runId = searchParams.get("run_id");
    const since = searchParams.get("since");
    const until = searchParams.get("until");
    const status = searchParams.get("status");

    const conditions = [];
    if (severity) conditions.push(eq(auditLogs.severity, severity));
    if (runId) conditions.push(eq(auditLogs.runId, runId));
    if (since) conditions.push(gte(auditLogs.createdAt, new Date(since)));
    if (until) conditions.push(lte(auditLogs.createdAt, new Date(until)));
    if (status) conditions.push(eq(auditLogs.status, status));

    const rows = await db
      .select()
      .from(auditLogs)
      .where(conditions.length > 0 ? and(...conditions) : undefined)
      .orderBy(desc(auditLogs.createdAt))
      .limit(limit);

    return NextResponse.json({
      logs: rows.map((r) => ({
        ...r,
        createdAt: r.createdAt.toISOString(),
        inputTokens: r.inputTokens ?? 0,
        outputTokens: r.outputTokens ?? 0,
        costCents: r.costCents ?? 0,
        durationMs: r.durationMs ?? 0,
      })),
      total: rows.length,
    });
  } catch (error) {
    console.error("Failed to load audit logs:", error);
    return NextResponse.json(
      { error: "Failed to load audit logs" },
      { status: 500 }
    );
  }
}

// Gateway writes audit events here
export async function POST(req: NextRequest) {
  try {
    // Verify internal service token (unconditional — reject if env var not set)
    const serviceToken = req.headers.get("x-able-service-token");
    const expectedToken = process.env.ABLE_SERVICE_TOKEN || "";
    if (!expectedToken || serviceToken !== expectedToken) {
      return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    }

    const body = await req.json();
    const {
      run_id,
      organization_id,
      agent_role,
      task,
      content,
      thinking_steps,
      tool_calls,
      provider_used,
      model_used,
      input_tokens,
      output_tokens,
      cost_cents,
      duration_ms,
      severity,
      status,
    } = body;

    if (!run_id || !agent_role || !task) {
      return NextResponse.json({ error: "run_id, agent_role, and task required" }, { status: 400 });
    }

    const [inserted] = await db
      .insert(auditLogs)
      .values({
        runId: run_id,
        organizationId: organization_id ?? null,
        agentRole: agent_role,
        task,
        content: content ?? null,
        thinkingSteps: thinking_steps ?? null,
        toolCalls: tool_calls ?? null,
        providerUsed: provider_used ?? null,
        modelUsed: model_used ?? null,
        inputTokens: input_tokens ?? 0,
        outputTokens: output_tokens ?? 0,
        costCents: cost_cents ?? 0,
        durationMs: duration_ms ?? 0,
        severity: severity ?? "info",
        status: status ?? "completed",
      })
      .returning({ id: auditLogs.id });

    return NextResponse.json({ success: true, id: inserted.id });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to create audit log" },
      { status: 500 }
    );
  }
}
