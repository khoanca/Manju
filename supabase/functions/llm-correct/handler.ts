// Proxy pass 2 có tính phí (US-605/606): nhận 1 chunk text + JWT user,
// kiểm tra số dư → gọi OpenRouter bằng key SERVER → trừ credit theo usage
// thật (RPC spend_credits, idempotent theo requestId) → trả text đã sửa.
// Chunking + _guard/_clean_output vẫn nằm ở client Python (app/correct.py).

import {
  computeCredits,
  env,
  type FetchFn,
  json,
  jwtSub,
  type PricingRate,
  restGet,
  rpc,
  type Usage,
} from "../_shared/billing.ts";

// Copy của _SYSTEM_PROMPT trong app/correct.py — sửa 1 nơi PHẢI sửa nơi kia.
const SYSTEM_PROMPT = "Bạn là công cụ soát lỗi transcript cuộc họp tiếng Việt có pha thuật ngữ " +
  "tiếng Anh. Nhiệm vụ DUY NHẤT: tìm những cụm từ bị phiên âm sai từ tiếng " +
  "Anh sang âm tiết tiếng Việt và thay bằng đúng từ tiếng Anh gốc. " +
  "Cụm nghi vấn đọc lên giống phát âm của thuật ngữ nào trong danh sách thì " +
  "thay bằng đúng thuật ngữ đó (VD: 'cu bơ nét ét' → 'Kubernetes', 'đíp lôi' " +
  "→ 'deploy'), không diễn đạt lại bằng từ tiếng Việt khác. " +
  "Giữ nguyên toàn bộ nội dung khác: không tóm tắt, không thêm bớt câu, " +
  "không sửa văn phong, không sửa chính tả tiếng Việt thông thường. " +
  "Trả về đúng phần text đã sửa, không lời giải thích, không markdown.";

const MAX_TEXT_CHARS = 2200; // CHUNK_CHARS 1800 ×1.2 — quá dài là lỗi client
const PREFLIGHT_MARGIN = 1.5; // ước lượng token phải dư dả để không âm ví
const MAX_CALLS_PER_MIN = 30; // chống JWT lộ đốt tiền server (risk #3 plan)

interface CorrectBody {
  text?: string;
  glossary?: string;
  context?: string;
  requestId?: string;
  ping?: boolean;
}

// Bản TS của _prompt_for trong app/correct.py.
function userPrompt(text: string, glossary: string, context: string): string {
  const parts: string[] = [];
  if (glossary) {
    parts.push(`Danh sách thuật ngữ / tên riêng cần nhận đúng: ${glossary}`);
  }
  if (context) {
    parts.push(
      "Các câu ngay trước đó trong cuộc họp (chỉ để hiểu ngữ cảnh, " +
        "KHÔNG đưa vào kết quả):\n" + context,
    );
  }
  parts.push("Text cần soát:\n" + text);
  return parts.join("\n\n");
}

async function overRateLimit(sub: string, fetchFn: FetchFn): Promise<boolean> {
  const oneMinAgo = new Date(Date.now() - 60_000).toISOString();
  const rows = await restGet(
    `credit_ledger?user_id=eq.${sub}&reason=eq.llm_correct` +
      `&created_at=gte.${encodeURIComponent(oneMinAgo)}&select=id` +
      `&limit=${MAX_CALLS_PER_MIN + 1}`,
    fetchFn,
  );
  return rows.length > MAX_CALLS_PER_MIN;
}

async function callOpenRouter(
  body: CorrectBody,
  fetchFn: FetchFn,
): Promise<Response> {
  const base = Deno.env.get("OPENROUTER_URL") ?? "https://openrouter.ai/api/v1";
  return await fetchFn(`${base}/chat/completions`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env("OPENROUTER_API_KEY")}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: Deno.env.get("OPENROUTER_MODEL") ?? "anthropic/claude-haiku-4.5",
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        {
          role: "user",
          content: userPrompt(
            body.text ?? "",
            body.glossary ?? "",
            body.context ?? "",
          ),
        },
      ],
    }),
  });
}

export async function handleLlmCorrect(
  req: Request,
  fetchFn: FetchFn = fetch,
): Promise<Response> {
  if (req.method !== "POST") return json(405, { error: "method_not_allowed" });
  const sub = jwtSub(req);
  if (!sub) return json(401, { error: "unauthorized" });

  let body: CorrectBody;
  try {
    body = (await req.json()) as CorrectBody;
  } catch {
    return json(400, { error: "bad_json" });
  }
  if (body.ping) return json(200, { ok: true }); // warm-up lúc mở phiên live

  const text = (body.text ?? "").trim();
  if (!text || !body.requestId) {
    return json(400, { error: "missing_text_or_request_id" });
  }
  if (text.length > MAX_TEXT_CHARS) {
    return json(413, { error: "text_too_long", maxChars: MAX_TEXT_CHARS });
  }

  const model = Deno.env.get("OPENROUTER_MODEL") ?? "anthropic/claude-haiku-4.5";
  const rates = (await restGet(
    `pricing_rates?model=eq.${encodeURIComponent(model)}&active=is.true`,
    fetchFn,
  )) as PricingRate[];
  if (!rates.length) return json(500, { error: "no_pricing_rate", model });
  const rate = rates[0];

  const wallets = (await restGet(
    `wallets?user_id=eq.${sub}&select=balance_credits`,
    fetchFn,
  )) as { balance_credits: number }[];
  const balance = wallets[0]?.balance_credits ?? 0;

  // Pre-flight: ước token ≈ ký_tự/3 × hệ số an toàn — chặn 402 TRƯỚC khi
  // tốn tiền OpenRouter. Usage thật vẫn có thể vượt ≤1 call (clamp-0 trong RPC).
  const estTokens = Math.ceil(text.length / 3);
  const estimated = Math.ceil(
    computeCredits(
      { model, prompt_tokens: estTokens, completion_tokens: estTokens },
      rate,
    ) * PREFLIGHT_MARGIN,
  );
  if (balance < estimated) {
    return json(402, {
      error: "insufficient_credits",
      balanceCredits: balance,
      estimatedCredits: estimated,
    });
  }

  if (await overRateLimit(sub, fetchFn)) {
    return json(429, { error: "rate_limited" });
  }

  const orResp = await callOpenRouter(body, fetchFn);
  if (orResp.status === 429) return json(429, { error: "rate_limited" });
  if (!orResp.ok) {
    return json(502, { error: "upstream_error", status: orResp.status });
  }
  const or = (await orResp.json()) as {
    choices?: { message?: { content?: unknown } }[];
    usage?: { prompt_tokens?: number; completion_tokens?: number };
  };
  const content = or.choices?.[0]?.message?.content;
  if (typeof content !== "string") {
    return json(502, { error: "upstream_bad_response" });
  }
  // usage thiếu (hiếm) → dùng estimate, thà trừ dư còn hơn phát miễn phí.
  const usage: Usage = {
    model,
    prompt_tokens: or.usage?.prompt_tokens ?? estTokens,
    completion_tokens: or.usage?.completion_tokens ?? estTokens,
  };

  const spent = computeCredits(usage, rate);
  const after = (await rpc(
    "spend_credits",
    {
      p_user_id: sub,
      p_credits: spent,
      p_request_id: body.requestId,
      p_usage: usage,
    },
    fetchFn,
  )) as number;

  // credits.* = milli-credit (×1000) — client chia 1000 khi hiển thị.
  return json(200, {
    text: content,
    usage: {
      promptTokens: usage.prompt_tokens,
      completionTokens: usage.completion_tokens,
    },
    credits: { spentCredits: spent, balanceCredits: after },
  });
}
