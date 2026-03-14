import {
  pgTable,
  text,
  timestamp,
  boolean,
  integer,
  jsonb,
  uuid,
  varchar,
  real,
  index,
} from "drizzle-orm/pg-core";

// ══════════════════════════════════════════════════════════════
// NEXTAUTH TABLES
// ══════════════════════════════════════════════════════════════

export const users = pgTable("users", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: text("name"),
  email: text("email").unique().notNull(),
  emailVerified: timestamp("email_verified", { mode: "date" }),
  image: text("image"),
  passwordHash: text("password_hash"),
  role: varchar("role", { length: 20 }).default("member").notNull(), // "owner" | "admin" | "member"
  organizationId: uuid("organization_id").references(() => organizations.id),
  createdAt: timestamp("created_at").defaultNow().notNull(),
});

export const accounts = pgTable("accounts", {
  id: uuid("id").defaultRandom().primaryKey(),
  userId: uuid("user_id").references(() => users.id, { onDelete: "cascade" }).notNull(),
  type: text("type").notNull(),
  provider: text("provider").notNull(),
  providerAccountId: text("provider_account_id").notNull(),
  refresh_token: text("refresh_token"),
  access_token: text("access_token"),
  expires_at: integer("expires_at"),
  token_type: text("token_type"),
  scope: text("scope"),
  id_token: text("id_token"),
  session_state: text("session_state"),
});

export const sessions = pgTable("sessions", {
  id: uuid("id").defaultRandom().primaryKey(),
  sessionToken: text("session_token").unique().notNull(),
  userId: uuid("user_id").references(() => users.id, { onDelete: "cascade" }).notNull(),
  expires: timestamp("expires", { mode: "date" }).notNull(),
});

// ══════════════════════════════════════════════════════════════
// MULTI-TENANT: ORGANIZATIONS
// ══════════════════════════════════════════════════════════════

export const organizations = pgTable("organizations", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: text("name").notNull(),
  slug: varchar("slug", { length: 64 }).unique().notNull(),
  plan: varchar("plan", { length: 20 }).default("free").notNull(), // "free" | "pro" | "enterprise"
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

// ══════════════════════════════════════════════════════════════
// CLIENT SETTINGS (per-organization)
// ══════════════════════════════════════════════════════════════

export const clientSettings = pgTable("client_settings", {
  id: uuid("id").defaultRandom().primaryKey(),
  organizationId: uuid("organization_id").references(() => organizations.id, { onDelete: "cascade" }).notNull(),

  // Dynamic API Keys (encrypted at rest via application layer)
  anthropicApiKey: text("anthropic_api_key"),       // encrypted
  openrouterApiKey: text("openrouter_api_key"),     // encrypted
  telegramBotToken: text("telegram_bot_token"),     // encrypted

  // Billing isolation
  billingEnabled: boolean("billing_enabled").default(false).notNull(),
  monthlyBudgetCents: integer("monthly_budget_cents").default(0),
  currentSpendCents: integer("current_spend_cents").default(0),

  // Agent config
  defaultModel: varchar("default_model", { length: 64 }).default("claude-sonnet-4-6"),
  maxTokensPerRequest: integer("max_tokens_per_request").default(16384),
  temperature: real("temperature").default(0.6),

  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

// ══════════════════════════════════════════════════════════════
// FEATURE FLAGS (MCP Skill/Tool Toggles)
// ══════════════════════════════════════════════════════════════

export const featureFlags = pgTable("feature_flags", {
  id: uuid("id").defaultRandom().primaryKey(),
  organizationId: uuid("organization_id").references(() => organizations.id, { onDelete: "cascade" }),

  // null organizationId = global default
  toolName: varchar("tool_name", { length: 128 }).notNull(),
  displayName: text("display_name").notNull(),
  description: text("description"),
  category: varchar("category", { length: 32 }).notNull(), // "github" | "deploy" | "infra" | "ai"
  enabled: boolean("enabled").default(true).notNull(),
  requiresApproval: boolean("requires_approval").default(true).notNull(),
  riskLevel: varchar("risk_level", { length: 10 }).default("medium"), // "low" | "medium" | "high"

  updatedAt: timestamp("updated_at").defaultNow().notNull(),
  updatedBy: uuid("updated_by").references(() => users.id),
}, (table) => [
  index("ff_org_tool_idx").on(table.organizationId, table.toolName),
]);

// ══════════════════════════════════════════════════════════════
// AUDIT LOGS
// ══════════════════════════════════════════════════════════════

export const auditLogs = pgTable("audit_logs", {
  id: uuid("id").defaultRandom().primaryKey(),
  runId: varchar("run_id", { length: 64 }).notNull(),
  organizationId: uuid("organization_id").references(() => organizations.id),

  agentRole: varchar("agent_role", { length: 32 }).notNull(),  // "scanner" | "auditor" | "executor" | "coordinator"
  task: text("task").notNull(),
  content: text("content"),                                       // Agent's response/output

  // Deep semantic log fields
  thinkingSteps: jsonb("thinking_steps"),  // Array of internal reasoning steps
  toolCalls: jsonb("tool_calls"),          // Array of tool invocations with args/results
  providerUsed: varchar("provider_used", { length: 64 }),
  modelUsed: varchar("model_used", { length: 128 }),
  inputTokens: integer("input_tokens").default(0),
  outputTokens: integer("output_tokens").default(0),
  costCents: integer("cost_cents").default(0),
  durationMs: integer("duration_ms").default(0),

  // Classification
  severity: varchar("severity", { length: 10 }).default("info"),  // "info" | "warning" | "error" | "critical"
  status: varchar("status", { length: 16 }).default("completed"), // "running" | "completed" | "failed" | "blocked"

  createdAt: timestamp("created_at").defaultNow().notNull(),
}, (table) => [
  index("audit_run_idx").on(table.runId),
  index("audit_org_idx").on(table.organizationId),
  index("audit_created_idx").on(table.createdAt),
]);

// ══════════════════════════════════════════════════════════════
// TYPE EXPORTS
// ══════════════════════════════════════════════════════════════

export type User = typeof users.$inferSelect;
export type Organization = typeof organizations.$inferSelect;
export type ClientSetting = typeof clientSettings.$inferSelect;
export type FeatureFlag = typeof featureFlags.$inferSelect;
export type AuditLog = typeof auditLogs.$inferSelect;
