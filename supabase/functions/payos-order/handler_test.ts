// deno test --allow-env  (chạy từ supabase/functions/)
import { assertEquals } from "jsr:@std/assert@1";
import { handlePayosOrder } from "./handler.ts";
import { handlePayosWebhook } from "../payos-webhook/handler.ts";
import { payosSignature } from "../_shared/billing.ts";

Deno.env.set("SUPABASE_URL", "http://supabase.test");
Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", "service-key");
Deno.env.set("PAYOS_CLIENT_ID", "cid");
Deno.env.set("PAYOS_API_KEY", "akey");
Deno.env.set("PAYOS_CHECKSUM_KEY", "csum");

const PKG = { code: "p50k", amount_vnd: 50000, credits: 50000000 };
const ORDER_ROW = { id: "order-uuid-1", provider_order_code: 123 };

function makeReq(body: unknown, jwt?: string): Request {
  const headers: Record<string, string> = {};
  if (jwt !== "") {
    headers.authorization = `Bearer h.${btoa(JSON.stringify({ sub: "user-a" }))}.s`;
  }
  return new Request("http://edge.test/", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

// deno-lint-ignore no-explicit-any
function stubFetch(opts: { pkg?: unknown[]; payosStatus?: number; payosBody?: any; rpcResult?: number }) {
  const calls: { url: string; method: string }[] = [];
  const fetchFn = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? "GET" });
    const reply = (body: unknown, status = 200) =>
      Promise.resolve(new Response(JSON.stringify(body), { status }));
    if (url.includes("/rest/v1/topup_packages")) return reply(opts.pkg ?? [PKG]);
    if (url.includes("/rest/v1/topup_orders") && init?.method === "POST") return reply([ORDER_ROW]);
    if (url.includes("/rest/v1/topup_orders") && init?.method === "PATCH") return reply({});
    if (url.includes("/rest/v1/rpc/apply_topup")) return reply(opts.rpcResult ?? 50000000);
    if (url.includes("payos.vn/v2/payment-requests")) {
      return reply(
        opts.payosBody ?? { code: "00", data: { checkoutUrl: "https://pay.payos.vn/web/abc123", qrCode: "qr" } },
        opts.payosStatus ?? 200,
      );
    }
    throw new Error(`unexpected fetch: ${url}`);
  };
  return { fetchFn: fetchFn as typeof fetch, calls };
}

Deno.test("payos-order: 200 — tạo đơn + trả checkoutUrl/credits", async () => {
  const { fetchFn } = stubFetch({});
  const resp = await handlePayosOrder(makeReq({ packageCode: "p50k" }), fetchFn);
  assertEquals(resp.status, 200);
  const body = await resp.json();
  assertEquals(body.checkoutUrl, "https://pay.payos.vn/web/abc123");
  assertEquals(body.credits, 50000000);
  assertEquals(body.orderId, "order-uuid-1");
});

Deno.test("payos-order: 404 gói lạ; 401 không JWT", async () => {
  const { fetchFn } = stubFetch({ pkg: [] });
  assertEquals((await handlePayosOrder(makeReq({ packageCode: "zzz" }), fetchFn)).status, 404);
  const noAuth = new Request("http://edge.test/", { method: "POST", body: "{}" });
  assertEquals((await handlePayosOrder(noAuth, stubFetch({}).fetchFn)).status, 401);
});

Deno.test("payos-order: PayOS lỗi → 502 + đơn chuyển failed", async () => {
  const { fetchFn, calls } = stubFetch({ payosStatus: 500, payosBody: {} });
  const resp = await handlePayosOrder(makeReq({ packageCode: "p50k" }), fetchFn);
  assertEquals(resp.status, 502);
  assertEquals(
    calls.some((c) => c.method === "PATCH" && c.url.includes("topup_orders")),
    true,
  );
});

Deno.test("payos-webhook: chữ ký đúng + code 00 → apply_topup, 200", async () => {
  const data = { orderCode: 123, amount: 50000, reference: "FT123" };
  const signature = await payosSignature(data, "csum");
  const { fetchFn, calls } = stubFetch({ rpcResult: 50000000 });
  const req = new Request("http://edge.test/", {
    method: "POST",
    body: JSON.stringify({ code: "00", data, signature }),
  });
  const resp = await handlePayosWebhook(req, fetchFn);
  assertEquals(resp.status, 200);
  assertEquals(await resp.json(), { ok: true, applied: true, balanceCredits: 50000000 });
  assertEquals(calls.some((c) => c.url.includes("rpc/apply_topup")), true);
});

Deno.test("payos-webhook: chữ ký sai → 401, KHÔNG gọi apply_topup", async () => {
  const { fetchFn, calls } = stubFetch({});
  const req = new Request("http://edge.test/", {
    method: "POST",
    body: JSON.stringify({ code: "00", data: { orderCode: 123 }, signature: "gia-mao" }),
  });
  assertEquals((await handlePayosWebhook(req, fetchFn)).status, 401);
  assertEquals(calls.some((c) => c.url.includes("rpc/apply_topup")), false);
});

Deno.test("payos-webhook: code != 00 → đơn failed, không cộng ví, vẫn 200", async () => {
  const data = { orderCode: 123 };
  const signature = await payosSignature(data, "csum");
  const { fetchFn, calls } = stubFetch({});
  const req = new Request("http://edge.test/", {
    method: "POST",
    body: JSON.stringify({ code: "01", data, signature }),
  });
  const resp = await handlePayosWebhook(req, fetchFn);
  assertEquals(resp.status, 200);
  assertEquals(await resp.json(), { ok: true, applied: false });
  assertEquals(calls.some((c) => c.url.includes("rpc/apply_topup")), false);
  assertEquals(calls.some((c) => c.method === "PATCH"), true);
});
