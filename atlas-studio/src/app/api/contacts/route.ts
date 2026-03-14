import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { contacts } from "@/db/schema";
import { eq, desc } from "drizzle-orm";

export async function GET() {
  try {
    const rows = await db.select().from(contacts).orderBy(desc(contacts.createdAt));
    return NextResponse.json({ contacts: rows });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    if (!body.name) return NextResponse.json({ error: "name required" }, { status: 400 });
    const [row] = await db.insert(contacts).values({
      name: body.name,
      email: body.email || null,
      phone: body.phone || null,
      company: body.company || null,
      title: body.title || null,
      source: body.source || "manual",
      status: body.status || "lead",
      notes: body.notes || null,
      tags: body.tags || [],
      organizationId: body.organization_id || null,
    }).returning();
    return NextResponse.json({ success: true, contact: row });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function PUT(req: NextRequest) {
  try {
    const body = await req.json();
    if (!body.id) return NextResponse.json({ error: "id required" }, { status: 400 });
    const updates: Record<string, any> = { updatedAt: new Date() };
    for (const key of ["name", "email", "phone", "company", "title", "source", "status", "notes", "tags"]) {
      if (body[key] !== undefined) updates[key] = body[key];
    }
    const [row] = await db.update(contacts).set(updates).where(eq(contacts.id, body.id)).returning();
    if (!row) return NextResponse.json({ error: "not found" }, { status: 404 });
    return NextResponse.json({ success: true, contact: row });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function DELETE(req: NextRequest) {
  try {
    const { id } = await req.json();
    if (!id) return NextResponse.json({ error: "id required" }, { status: 400 });
    await db.delete(contacts).where(eq(contacts.id, id));
    return NextResponse.json({ success: true });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
