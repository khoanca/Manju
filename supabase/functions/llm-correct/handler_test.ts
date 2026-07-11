// deno test --allow-env  (chạy từ supabase/functions/)
// Stub fetch theo URL — không network. Fixtures dùng chung với pytest
// (tests/test_correct.py đọc cùng file) để 2 phía khớp wire schema.

import { assertEquals } from "jsr:@std/assert@1";
import { handleLlmCorrect } from "./handler.ts";
import { computeCredits, payosSignature } from "../_shared/billing.ts";
import orFixture from "./fixtures/openrouter_response.json" with { type: "json" };
import ok200 from "./fixtures/llm_correct_200.json" with { type: "json" };
import err402 from "./fixtures/llm_correct_402.json" with { type: "json" };

Deno.env.set("SUPABASE_URL", "http://supabase.test");
Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", "service-key");
Deno.env.set("OPENROUTER_API_KEY", "or-key");

const RATE = {
  model: "anthropic/claude-haiku-4.5",
  credits_per_1k_prompt: 1000,
  credits_per_1k_completion: 5000,
};

function makeJwt(sub: string): string {
  return `h.${btoa(JSON.stringify({ sub }))}.s`;
}

function makeReq(body: unknown, jwt = makeJwt("user-a")): Request {
  return new Request("http://edge.test/llm-correct", {
    method: "POST",
    headers: { authorization: `Bearer ${jwt}` },
    body: JSON.stringify(body),
  });
}

// deno-lint-ignore no-explicit-any
function stubFetch(opts: { balance: number; rpcResult?: number; orStatus?: number; orBody?: any }) {
  const calls: string[] = [];
  const fetchFn = (input: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
    const url = String(input);
    calls.push(url);
    const reply = (body: unknown, status = 200) =>
      Promise.resolve(
        new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
      );
    if (url.includes("/rest/v1/pricing_rates")) return reply([RATE]);
    if (url.includes("/rest/v1/wallets")) return reply([{ balance_credits: opts.balance }]);
    if (url.includes("/rest/v1/credit_ledger")) return reply([]);
    if (url.includes("/rest/v1/rpc/spend_credits")) return reply(opts.rpcResult ?? 0);
    if (url.includes("/chat/completions")) {
      return reply(opts.orBody ?? orFixture, opts.orStatus ?? 200);
    }
    throw new Error(`unexpected fetch: ${url}`);
  };
  return { fetchFn: fetchFn as typeof fetch, calls };
}

Deno.test("computeCredits làm tròn lên từng vế", () => {
  // 120×1000/1000=120; 45×5000/1000=225 → 345 (khớp fixture llm_correct_200)
  assertEquals(computeCredits({ model: "m", prompt_tokens: 120, completion_tokens: 45 }, RATE), 345);
  assertEquals(computeCredits({ model: "m", prompt_tokens: 1, completion_tokens: 1 }, RATE), 6); // ceil(1)+ceil(5)
});

Deno.test("200 happy path — trả text + usage + credits khớp fixture", async () => {
  const { fetchFn } = stubFetch({ balance: 10000, rpcResult: 9655 });
  const resp = await handleLlmCorrect(
    makeReq({ text: "anh em đíp lôi lên cu bơ nét", requestId: "11111111-1111-1111-1111-111111111111" }),
    fetchFn,
  );
  assertEquals(resp.status, 200);
  assertEquals(await resp.json(), ok200);
});

Deno.test("402 khi số dư dưới pre-flight estimate — KHÔNG gọi OpenRouter", async () => {
  const { fetchFn, calls } = stubFetch({ balance: 10 });
  const resp = await handleLlmCorrect(
    makeReq({ text: "xin chào cu bơ nét ét", requestId: "22222222-2222-2222-2222-222222222222" }),
    fetchFn,
  );
  assertEquals(resp.status, 402);
  assertEquals(await resp.json(), err402);
  assertEquals(calls.some((u) => u.includes("/chat/completions")), false);
});

Deno.test("502 khi OpenRouter lỗi — không trừ credit", async () => {
  const { fetchFn, calls } = stubFetch({ balance: 10000, orStatus: 500, orBody: {} });
  const resp = await handleLlmCorrect(
    makeReq({ text: "abc", requestId: "33333333-3333-3333-3333-333333333333" }),
    fetchFn,
  );
  assertEquals(resp.status, 502);
  assertEquals(calls.some((u) => u.includes("rpc/spend_credits")), false);
});

Deno.test("413 khi text quá dài", async () => {
  const { fetchFn } = stubFetch({ balance: 10000 });
  const resp = await handleLlmCorrect(
    makeReq({ text: "x".repeat(2201), requestId: "44444444-4444-4444-4444-444444444444" }),
    fetchFn,
  );
  assertEquals(resp.status, 413);
});

Deno.test("400 khi thiếu requestId; 401 khi không có JWT; ping → 200", async () => {
  const { fetchFn } = stubFetch({ balance: 10000 });
  assertEquals((await handleLlmCorrect(makeReq({ text: "abc" }), fetchFn)).status, 400);
  const noAuth = new Request("http://edge.test/llm-correct", { method: "POST", body: "{}" });
  assertEquals((await handleLlmCorrect(noAuth, fetchFn)).status, 401);
  assertEquals((await handleLlmCorrect(makeReq({ ping: true }), fetchFn)).status, 200);
});

Deno.test("payosSignature — HMAC hex ổn định, key sort alphabet", async () => {
  const sig = await payosSignature(
    { orderCode: 123, amount: 50000, description: "nap", cancelUrl: "c", returnUrl: "r" },
    "secret",
  );
  // amount=50000&cancelUrl=c&description=nap&orderCode=123&returnUrl=r
  assertEquals(sig.length, 64);
  const sig2 = await payosSignature(
    { returnUrl: "r", amount: 50000, cancelUrl: "c", orderCode: 123, description: "nap" },
    "secret",
  );
  assertEquals(sig, sig2); // thứ tự field không đổi chữ ký
});
