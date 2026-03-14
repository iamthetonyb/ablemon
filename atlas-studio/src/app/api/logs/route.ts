import { db } from "@/db";
import { agentLogs } from "@/db/schema";
import { NextResponse } from "next/server";

export async function POST(req: Request) {
  // Simple API key or internal validation could be added here
  try {
    const body = await req.json();
    
    await db.insert(agentLogs).values({
      runId: body.runId || "unknown_run",
      agentRole: body.agentRole || "system",
      task: body.task || "telemetry_ping",
      content: body.content || null,
      metadata: body.metadata || {},
    });

    return NextResponse.json({ success: true });
  } catch (error: any) {
    console.error("Telemetry ingest error:", error);
    return NextResponse.json({ error: "Failed to insert log" }, { status: 500 });
  }
}
