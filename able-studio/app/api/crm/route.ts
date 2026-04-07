import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { contacts, deals } from "@/drizzle/schema";
import { desc, eq, and } from "drizzle-orm";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = req.nextUrl;
    const orgId = searchParams.get("org_id");
    const stage = searchParams.get("stage");

    const contactConditions = [];
    const dealConditions = [];
    if (orgId) {
      contactConditions.push(eq(contacts.organizationId, orgId));
      dealConditions.push(eq(deals.organizationId, orgId));
    }
    if (stage) {
      contactConditions.push(eq(contacts.stage, stage));
    }

    const [contactRows, dealRows] = await Promise.all([
      db
        .select()
        .from(contacts)
        .where(contactConditions.length > 0 ? and(...contactConditions) : undefined)
        .orderBy(desc(contacts.updatedAt)),
      db
        .select()
        .from(deals)
        .where(dealConditions.length > 0 ? and(...dealConditions) : undefined)
        .orderBy(desc(deals.updatedAt)),
    ]);

    return NextResponse.json({
      contacts: contactRows.map((c) => ({
        ...c,
        createdAt: c.createdAt.toISOString(),
        updatedAt: c.updatedAt.toISOString(),
        lastContactedAt: c.lastContactedAt?.toISOString() ?? null,
      })),
      deals: dealRows.map((d) => ({
        ...d,
        createdAt: d.createdAt.toISOString(),
        updatedAt: d.updatedAt.toISOString(),
        expectedCloseDate: d.expectedCloseDate?.toISOString() ?? null,
      })),
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Failed to load CRM data" },
      { status: 500 }
    );
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { type, org_id } = body;

    if (type === "deal") {
      const { title, value_cents, contact_id, stage, notes: dealNotes, probability } = body;
      if (!title) {
        return NextResponse.json({ error: "title required for deal" }, { status: 400 });
      }
      const [inserted] = await db
        .insert(deals)
        .values({
          organizationId: org_id ?? null,
          contactId: contact_id ?? null,
          title,
          valueCents: value_cents ?? 0,
          stage: stage ?? "discovery",
          probability: probability ?? 10,
          notes: dealNotes ?? null,
        })
        .returning();
      return NextResponse.json({
        success: true,
        deal: {
          ...inserted,
          createdAt: inserted.createdAt.toISOString(),
          updatedAt: inserted.updatedAt.toISOString(),
          expectedCloseDate: inserted.expectedCloseDate?.toISOString() ?? null,
        },
      });
    }

    // Default: contact
    const { name, email, phone, company, stage, source, notes: contactNotes } = body;
    if (!name) {
      return NextResponse.json({ error: "name required for contact" }, { status: 400 });
    }
    const [inserted] = await db
      .insert(contacts)
      .values({
        organizationId: org_id ?? null,
        name,
        email: email ?? null,
        phone: phone ?? null,
        company: company ?? null,
        stage: stage ?? "lead",
        source: source ?? null,
        notes: contactNotes ?? null,
      })
      .returning();
    return NextResponse.json({
      success: true,
      contact: {
        ...inserted,
        createdAt: inserted.createdAt.toISOString(),
        updatedAt: inserted.updatedAt.toISOString(),
        lastContactedAt: inserted.lastContactedAt?.toISOString() ?? null,
      },
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Failed to create CRM record" },
      { status: 500 }
    );
  }
}

export async function PUT(req: NextRequest) {
  try {
    const body = await req.json();
    const { type, id, stage, probability, notes } = body;

    if (!id) {
      return NextResponse.json({ error: "id required" }, { status: 400 });
    }

    if (type === "deal") {
      const updates: Record<string, unknown> = { updatedAt: new Date() };
      if (stage !== undefined) updates.stage = stage;
      if (probability !== undefined) updates.probability = probability;
      if (notes !== undefined) updates.notes = notes;
      await db.update(deals).set(updates).where(eq(deals.id, id));
    } else {
      const updates: Record<string, unknown> = { updatedAt: new Date() };
      if (stage !== undefined) updates.stage = stage;
      if (notes !== undefined) updates.notes = notes;
      await db.update(contacts).set(updates).where(eq(contacts.id, id));
    }

    return NextResponse.json({ success: true });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Failed to update CRM record" },
      { status: 500 }
    );
  }
}
