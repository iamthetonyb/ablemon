import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { organizations, clientSettings } from "@/db/schema";
import { eq } from "drizzle-orm";
import { encrypt } from "@/lib/encryption";

export async function GET() {
  try {
    const orgs = await db
      .select({ id: organizations.id, name: organizations.name, slug: organizations.slug, plan: organizations.plan, createdAt: organizations.createdAt })
      .from(organizations);
    return NextResponse.json({ organizations: orgs });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    if (!body.name || !body.slug) return NextResponse.json({ error: "name and slug required" }, { status: 400 });

    const [org] = await db.insert(organizations).values({
      name: body.name,
      slug: body.slug.toLowerCase().replace(/[^a-z0-9-]/g, ""),
      plan: body.plan || "free",
    }).returning();

    await db.insert(clientSettings).values({ organizationId: org.id });
    return NextResponse.json({ success: true, organization: org });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function PUT(req: NextRequest) {
  try {
    const body = await req.json();
    if (!body.org_id) return NextResponse.json({ error: "org_id required" }, { status: 400 });

    const updates: Record<string, any> = { updatedAt: new Date() };
    if (body.anthropic_api_key) updates.anthropicApiKey = await encrypt(body.anthropic_api_key);
    if (body.openrouter_api_key) updates.openrouterApiKey = await encrypt(body.openrouter_api_key);
    if (body.telegram_bot_token) updates.telegramBotToken = await encrypt(body.telegram_bot_token);
    if (body.default_model) updates.defaultModel = body.default_model;
    if (body.max_tokens) updates.maxTokensPerRequest = body.max_tokens;
    if (body.temperature !== undefined) updates.temperature = body.temperature;
    if (body.monthly_budget_cents !== undefined) updates.monthlyBudgetCents = body.monthly_budget_cents;
    if (body.billing_enabled !== undefined) updates.billingEnabled = body.billing_enabled;

    const updated = await db.update(clientSettings).set(updates).where(eq(clientSettings.organizationId, body.org_id)).returning();
    if (updated.length === 0) return NextResponse.json({ error: "Client settings not found" }, { status: 404 });
    return NextResponse.json({ success: true });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
