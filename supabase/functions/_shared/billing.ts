// Helpers dùng chung cho Edge Functions billing (PRD FR-6).
// Không dùng supabase-js: gọi thẳng PostgREST bằng fetch — nhẹ, dễ stub khi test.
// Đơn vị credit trên wire = milli-credit (bigint ×1000, khớp 002_billing.sql).

export type FetchFn = typeof fetch;

export interface Usage {
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface PricingRate {
  model: string;
  credits_per_1k_prompt: number;
  credits_per_1k_completion: number;
}

export function env(name: string): string {
  const v = Deno.env.get(name);
  if (!v) throw new Error(`missing env ${name}`);
  return v;
}

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// verify_jwt=true: platform đã verify chữ ký trước khi vào function —
// ở đây chỉ decode payload lấy user id (sub), không verify lại.
export function jwtSub(req: Request): string | null {
  const auth = req.headers.get("authorization") ?? "";
  const token = auth.replace(/^Bearer\s+/i, "");
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const payload = JSON.parse(
      atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")),
    );
    return typeof payload.sub === "string" ? payload.sub : null;
  } catch {
    return null;
  }
}

// PostgREST với service_role — bỏ qua RLS, CHỈ dùng server-side.
function restHeaders(): Record<string, string> {
  const key = env("SUPABASE_SERVICE_ROLE_KEY");
  return {
    apikey: key,
    authorization: `Bearer ${key}`,
    "content-type": "application/json",
  };
}

export async function restGet(
  path: string,
  fetchFn: FetchFn = fetch,
): Promise<unknown[]> {
  const resp = await fetchFn(`${env("SUPABASE_URL")}/rest/v1/${path}`, {
    headers: restHeaders(),
  });
  if (!resp.ok) throw new Error(`postgrest GET ${path}: ${resp.status}`);
  return (await resp.json()) as unknown[];
}

export async function restPost(
  path: string,
  body: unknown,
  fetchFn: FetchFn = fetch,
): Promise<unknown[]> {
  const resp = await fetchFn(`${env("SUPABASE_URL")}/rest/v1/${path}`, {
    method: "POST",
    headers: { ...restHeaders(), prefer: "return=representation" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`postgrest POST ${path}: ${resp.status}`);
  return (await resp.json()) as unknown[];
}

export async function restPatch(
  path: string,
  body: unknown,
  fetchFn: FetchFn = fetch,
): Promise<void> {
  const resp = await fetchFn(`${env("SUPABASE_URL")}/rest/v1/${path}`, {
    method: "PATCH",
    headers: restHeaders(),
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`postgrest PATCH ${path}: ${resp.status}`);
}

export async function rpc(
  fn: string,
  args: Record<string, unknown>,
  fetchFn: FetchFn = fetch,
): Promise<unknown> {
  const resp = await fetchFn(`${env("SUPABASE_URL")}/rest/v1/rpc/${fn}`, {
    method: "POST",
    headers: restHeaders(),
    body: JSON.stringify(args),
  });
  if (!resp.ok) throw new Error(`postgrest RPC ${fn}: ${resp.status}`);
  return await resp.json();
}

// Milli-credit. Làm tròn LÊN từng vế — không bán rẻ hơn bảng giá.
export function computeCredits(usage: Usage, rate: PricingRate): number {
  const p = Math.ceil((usage.prompt_tokens * rate.credits_per_1k_prompt) / 1000);
  const c = Math.ceil(
    (usage.completion_tokens * rate.credits_per_1k_completion) / 1000,
  );
  return p + c;
}

// PayOS ký HMAC-SHA256 hex trên chuỗi "k1=v1&k2=v2..." (key sort alphabet).
// Dùng cho cả tạo payment-request lẫn verify webhook.
export async function payosSignature(
  data: Record<string, unknown>,
  checksumKey: string,
): Promise<string> {
  const msg = Object.keys(data)
    .sort()
    .map((k) => `${k}=${data[k] ?? ""}`)
    .join("&");
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(checksumKey),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(msg),
  );
  return [...new Uint8Array(sig)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
