import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { milestones } from "@/db/schema";
import { eq } from "drizzle-orm";

export async function GET() {
  try {
    const rows = await db.select().from(milestones).orderBy(milestones.targetDate);
    return NextResponse.json({ milestones: rows });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    if (!body.title || !body.target_date) return NextResponse.json({ error: "title and target_date required" }, { status: 400 });
    const [row] = await db.insert(milestones).values({
      title: body.title,
      description: body.description || null,
      targetDate: new Date(body.target_date),
      phase: body.phase || null,
      projectId: body.project_id || null,
      color: body.color || "#D4AF37",
    }).returning();
    return NextResponse.json({ success: true, milestone: row });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function PUT(req: NextRequest) {
  try {
    const body = await req.json();
    if (!body.id) return NextResponse.json({ error: "id required" }, { status: 400 });
    const updates: Record<string, any> = {};
    if (body.title !== undefined) updates.title = body.title;
    if (body.target_date !== undefined) updates.targetDate = new Date(body.target_date);
    if (body.completed_at !== undefined) updates.completedAt = body.completed_at ? new Date(body.completed_at) : null;
    if (body.phase !== undefined) updates.phase = body.phase;
    if (body.color !== undefined) updates.color = body.color;
    const [row] = await db.update(milestones).set(updates).where(eq(milestones.id, body.id)).returning();
    if (!row) return NextResponse.json({ error: "not found" }, { status: 404 });
    return NextResponse.json({ success: true, milestone: row });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
