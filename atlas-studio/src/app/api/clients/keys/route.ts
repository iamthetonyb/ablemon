import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { clientSettings } from "@/db/schema";
import { eq } from "drizzle-orm";
import { decrypt } from "@/lib/encryption";

/**
 * GET /api/clients/keys?org_id=<id>
 *
 * Called by Python gateway to retrieve a client's decrypted API keys.
 * This ensures billing isolation — each client uses their own keys.
 *
 * SECURITY: This endpoint should be protected by an internal service token
 * in production. For now, it's accessible only from the gateway.
 */
export async function GET(req: NextRequest) {
  const orgId = req.nextUrl.searchParams.get("org_id");

  if (!orgId) {
    return NextResponse.json({ error: "org_id required" }, { status: 400 });
  }

  // Verify internal service token
  const serviceToken = req.headers.get("x-atlas-service-token");
  const expectedToken = process.env.ATLAS_SERVICE_TOKEN;
  if (expectedToken && serviceToken !== expectedToken) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const [settings] = await db
    .select()
    .from(clientSettings)
    .where(eq(clientSettings.organizationId, orgId))
    .limit(1);

  if (!settings) {
    return NextResponse.json({ error: "Client not found" }, { status: 404 });
  }

  // Decrypt keys
  const keys: Record<string, string | null> = {
    anthropic_api_key: null,
    openrouter_api_key: null,
    telegram_bot_token: null,
  };

  try {
    if (settings.anthropicApiKey) keys.anthropic_api_key = await decrypt(settings.anthropicApiKey);
    if (settings.openrouterApiKey) keys.openrouter_api_key = await decrypt(settings.openrouterApiKey);
    if (settings.telegramBotToken) keys.telegram_bot_token = await decrypt(settings.telegramBotToken);
  } catch {
    return NextResponse.json({ error: "Decryption failed" }, { status: 500 });
  }

  return NextResponse.json({
    org_id: orgId,
    keys,
    config: {
      default_model: settings.defaultModel,
      max_tokens: settings.maxTokensPerRequest,
      temperature: settings.temperature,
      billing_enabled: settings.billingEnabled,
      monthly_budget_cents: settings.monthlyBudgetCents,
      current_spend_cents: settings.currentSpendCents,
    },
  });
}
