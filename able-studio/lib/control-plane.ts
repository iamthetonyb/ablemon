const CONTROL_BASE_URL =
  process.env.ABLE_CONTROL_API_BASE ||
  process.env.ABLE_GATEWAY_URL ||
  "";

/** True when a gateway URL is configured. */
export function isGatewayConfigured(): boolean {
  return !!CONTROL_BASE_URL;
}

function buildUrl(path: string) {
  const base = CONTROL_BASE_URL.endsWith("/")
    ? CONTROL_BASE_URL.slice(0, -1)
    : CONTROL_BASE_URL;
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}${suffix}`;
}

function buildHeaders(existing?: HeadersInit) {
  const headers = new Headers(existing);
  headers.set("Accept", "application/json");

  const serviceToken = process.env.ABLE_SERVICE_TOKEN;
  if (serviceToken) {
    headers.set("x-able-service-token", serviceToken);
  }

  return headers;
}

async function fetchControl<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(buildUrl(path), {
    ...init,
    headers: buildHeaders(init?.headers),
    cache: "no-store",
    signal: init?.signal ?? AbortSignal.timeout(5000),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `Control plane request failed (${response.status} ${response.statusText}): ${body.slice(0, 400)}`,
    );
  }

  return (await response.json()) as T;
}

export async function getToolCatalog(orgId?: string) {
  const params = orgId ? `?org_id=${encodeURIComponent(orgId)}` : "";
  return fetchControl<{
    organization_id: string;
    catalog: Array<Record<string, unknown>>;
    definitions: Array<Record<string, unknown>>;
    timestamp: string;
  }>(`/control/tools/catalog${params}`);
}

export async function getResources() {
  return fetchControl<{
    resources: Array<Record<string, unknown>>;
    timestamp: string;
  }>("/control/resources");
}

export async function getResource(resourceId: string) {
  return fetchControl<Record<string, unknown>>(
    `/control/resources/${encodeURIComponent(resourceId)}`,
  );
}

export async function performResourceAction(
  resourceId: string,
  action: string,
  approvedBy?: string,
) {
  return fetchControl<Record<string, unknown>>(
    `/control/resources/${encodeURIComponent(resourceId)}/action`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        action,
        approved_by: approvedBy,
      }),
    },
  );
}

export async function getCollections() {
  return fetchControl<{
    collections: Array<Record<string, unknown>>;
    timestamp: string;
  }>("/control/collections");
}

export async function getSetupWizard() {
  return fetchControl<Record<string, unknown>>("/control/setup-wizard");
}
