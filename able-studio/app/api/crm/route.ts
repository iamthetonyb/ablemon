import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { contacts, deals, organizations } from "@/drizzle/schema";
import { eq, desc, sql, count } from "drizzle-orm";

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const view = searchParams.get("view") || "all";

  try {
    const contactList = await db
      .select({
        id: contacts.id,
        name: contacts.name,
        email: contacts.email,
        phone: contacts.phone,
        company: contacts.company,
        stage: contacts.stage,
        source: contacts.source,
        notes: contacts.notes,
        lastContactedAt: contacts.lastContactedAt,
        createdAt: contacts.createdAt,
        organizationId: contacts.organizationId,
      })
      .from(contacts)
      .orderBy(desc(contacts.updatedAt))
      .limit(100);

    const dealList = await db
      .select({
        id: deals.id,
        title: deals.title,
        valueCents: deals.valueCents,
        stage: deals.stage,
        probability: deals.probability,
        expectedCloseDate: deals.expectedCloseDate,
        notes: deals.notes,
        contactId: deals.contactId,
        organizationId: deals.organizationId,
        createdAt: deals.createdAt,
      })
      .from(deals)
      .orderBy(desc(deals.updatedAt))
      .limit(100);

    // Pipeline summary
    const stageCounts = await db
      .select({
        stage: deals.stage,
        count: count(),
        totalValue: sql<number>`coalesce(sum(${deals.valueCents}), 0)`,
      })
      .from(deals)
      .groupBy(deals.stage);

    const contactCounts = await db
      .select({
        stage: contacts.stage,
        count: count(),
      })
      .from(contacts)
      .groupBy(contacts.stage);

    return NextResponse.json({
      contacts: contactList,
      deals: dealList,
      pipeline: {
        dealStages: stageCounts,
        contactStages: contactCounts,
      },
    });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  const type = body.type; // "contact" or "deal"

  try {
    if (type === "contact") {
      if (!body.name) {
        return NextResponse.json({ error: "name required" }, { status: 400 });
      }
      const [contact] = await db
        .insert(contacts)
        .values({
          name: body.name,
          email: body.email || null,
          phone: body.phone || null,
          company: body.company || null,
          stage: body.stage || "lead",
          source: body.source || null,
          notes: body.notes || null,
          organizationId: body.organization_id || null,
        })
        .returning();
      return NextResponse.json({ success: true, contact });
    }

    if (type === "deal") {
      if (!body.title) {
        return NextResponse.json({ error: "title required" }, { status: 400 });
      }
      const [deal] = await db
        .insert(deals)
        .values({
          title: body.title,
          valueCents: body.value_cents || 0,
          stage: body.stage || "discovery",
          probability: body.probability || 10,
          contactId: body.contact_id || null,
          organizationId: body.organization_id || null,
          notes: body.notes || null,
          expectedCloseDate: body.expected_close_date
            ? new Date(body.expected_close_date)
            : null,
        })
        .returning();
      return NextResponse.json({ success: true, deal });
    }

    return NextResponse.json({ error: "type must be 'contact' or 'deal'" }, { status: 400 });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function PUT(req: NextRequest) {
  const body = await req.json();

  try {
    if (body.type === "contact" && body.id) {
      const updates: Record<string, any> = { updatedAt: new Date() };
      if (body.name !== undefined) updates.name = body.name;
      if (body.email !== undefined) updates.email = body.email;
      if (body.phone !== undefined) updates.phone = body.phone;
      if (body.company !== undefined) updates.company = body.company;
      if (body.stage !== undefined) updates.stage = body.stage;
      if (body.source !== undefined) updates.source = body.source;
      if (body.notes !== undefined) updates.notes = body.notes;

      const [updated] = await db
        .update(contacts)
        .set(updates)
        .where(eq(contacts.id, body.id))
        .returning();
      return NextResponse.json({ success: true, contact: updated });
    }

    if (body.type === "deal" && body.id) {
      const updates: Record<string, any> = { updatedAt: new Date() };
      if (body.title !== undefined) updates.title = body.title;
      if (body.value_cents !== undefined) updates.valueCents = body.value_cents;
      if (body.stage !== undefined) updates.stage = body.stage;
      if (body.probability !== undefined) updates.probability = body.probability;
      if (body.notes !== undefined) updates.notes = body.notes;
      if (body.contact_id !== undefined) updates.contactId = body.contact_id;

      const [updated] = await db
        .update(deals)
        .set(updates)
        .where(eq(deals.id, body.id))
        .returning();
      return NextResponse.json({ success: true, deal: updated });
    }

    return NextResponse.json({ error: "type + id required" }, { status: 400 });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
