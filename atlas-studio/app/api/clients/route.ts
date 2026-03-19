import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { organizations, clientSettings, users } from "@/drizzle/schema";
import { eq } from "drizzle-orm";
import { encrypt, decrypt } from "@/lib/encryption";

/**
 * GET /api/clients
 *
 * List all organizations with their settings.
 */
export async function GET() {
  const orgs = await db
    .select({
      id: organizations.id,
      name: organizations.name,
      slug: organizations.slug,
      plan: organizations.plan,
      createdAt: organizations.createdAt,
    })
    .from(organizations);

  return NextResponse.json({ organizations: orgs });
}

/**
 * POST /api/clients
 *
 * Create a new organization (tenant).
 * Body: { name, slug, plan? }
 */
export async function POST(req: NextRequest) {
  const body = await req.json();

  if (!body.name || !body.slug) {
    return NextResponse.json({ error: "name and slug required" }, { status: 400 });
  }

  try {
    const [org] = await db
      .insert(organizations)
      .values({
        name: body.name,
        slug: body.slug.toLowerCase().replace(/[^a-z0-9-]/g, ""),
        plan: body.plan || "free",
      })
      .returning();

    // Create default client settings
    await db.insert(clientSettings).values({
      organizationId: org.id,
    });

    return NextResponse.json({ success: true, organization: org });
  } catch (e: any) {
    const message = e?.message?.includes("unique") ? "Organization slug already exists" : e?.message || "Failed to create organization";
    return NextResponse.json({ success: false, error: message }, { status: 400 });
  }
}

/**
 * PUT /api/clients
 *
 * Update client settings (including API keys).
 * Body: { org_id, anthropic_api_key?, openrouter_api_key?, telegram_bot_token?, ... }
 *
 * API keys are encrypted before storage.
 */
export async function PUT(req: NextRequest) {
  const body = await req.json();

  if (!body.org_id) {
    return NextResponse.json({ error: "org_id required" }, { status: 400 });
  }

  const updates: Record<string, any> = { updatedAt: new Date() };

  // Encrypt API keys before storing
  if (body.anthropic_api_key) {
    updates.anthropicApiKey = await encrypt(body.anthropic_api_key);
  }
  if (body.openrouter_api_key) {
    updates.openrouterApiKey = await encrypt(body.openrouter_api_key);
  }
  if (body.telegram_bot_token) {
    updates.telegramBotToken = await encrypt(body.telegram_bot_token);
  }

  // Non-sensitive settings
  if (body.default_model) updates.defaultModel = body.default_model;
  if (body.max_tokens) updates.maxTokensPerRequest = body.max_tokens;
  if (body.temperature !== undefined) updates.temperature = body.temperature;
  if (body.monthly_budget_cents !== undefined) updates.monthlyBudgetCents = body.monthly_budget_cents;
  if (body.billing_enabled !== undefined) updates.billingEnabled = body.billing_enabled;

  const updated = await db
    .update(clientSettings)
    .set(updates)
    .where(eq(clientSettings.organizationId, body.org_id))
    .returning();

  if (updated.length === 0) {
    return NextResponse.json({ error: "Client settings not found" }, { status: 404 });
  }

  return NextResponse.json({ success: true });
}
