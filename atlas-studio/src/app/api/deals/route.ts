import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { deals } from "@/db/schema";
import { eq, desc } from "drizzle-orm";

export async function GET() {
  try {
    const rows = await db.select().from(deals).orderBy(desc(deals.createdAt));
    return NextResponse.json({ deals: rows });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    if (!body.title || !body.contact_id) return NextResponse.json({ error: "title and contact_id required" }, { status: 400 });
    const [row] = await db.insert(deals).values({
      title: body.title,
      contactId: body.contact_id,
      organizationId: body.organization_id || null,
      valueCents: body.value_cents ?? 0,
      stage: body.stage || "discovery",
      probability: body.probability ?? 50,
      expectedCloseDate: body.expected_close_date ? new Date(body.expected_close_date) : null,
      notes: body.notes || null,
    }).returning();
    return NextResponse.json({ success: true, deal: row });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function PUT(req: NextRequest) {
  try {
    const body = await req.json();
    if (!body.id) return NextResponse.json({ error: "id required" }, { status: 400 });
    const updates: Record<string, any> = { updatedAt: new Date() };
    if (body.title !== undefined) updates.title = body.title;
    if (body.stage !== undefined) updates.stage = body.stage;
    if (body.value_cents !== undefined) updates.valueCents = body.value_cents;
    if (body.probability !== undefined) updates.probability = body.probability;
    if (body.notes !== undefined) updates.notes = body.notes;
    if (body.expected_close_date !== undefined) updates.expectedCloseDate = body.expected_close_date ? new Date(body.expected_close_date) : null;
    if (body.closed_at !== undefined) updates.closedAt = body.closed_at ? new Date(body.closed_at) : null;
    const [row] = await db.update(deals).set(updates).where(eq(deals.id, body.id)).returning();
    if (!row) return NextResponse.json({ error: "not found" }, { status: 404 });
    return NextResponse.json({ success: true, deal: row });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
