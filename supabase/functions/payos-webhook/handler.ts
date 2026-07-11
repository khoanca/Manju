// Webhook PayOS (US-604): verify chữ ký HMAC trên object `data` → cộng ví qua
// RPC apply_topup (idempotent: chỉ đơn 'pending' được chuyển 'paid'; webhook
// trùng/đơn lạ → no-op). Deploy với --no-verify-jwt (PayOS không có JWT).
// LUÔN trả 200 khi đã xử lý xong (kể cả duplicate) — PayOS retry khi non-2xx.

import {
  env,
  type FetchFn,
  json,
  payosSignature,
  restPatch,
  rpc,
} from "../_shared/billing.ts";

interface WebhookBody {
  code?: string; // "00" = thanh toán thành công
  desc?: string;
  data?: Record<string, unknown> & { orderCode?: number };
  signature?: string;
}

export async function handlePayosWebhook(
  req: Request,
  fetchFn: FetchFn = fetch,
): Promise<Response> {
  if (req.method !== "POST") return json(405, { error: "method_not_allowed" });

  let body: WebhookBody;
  try {
    body = (await req.json()) as WebhookBody;
  } catch {
    return json(400, { error: "bad_json" });
  }
  if (!body.data || !body.signature) {
    return json(400, { error: "missing_data_or_signature" });
  }

  const expected = await payosSignature(body.data, env("PAYOS_CHECKSUM_KEY"));
  if (expected !== body.signature) {
    return json(401, { error: "bad_signature" });
  }

  const orderCode = body.data.orderCode;
  if (typeof orderCode !== "number") {
    return json(400, { error: "missing_order_code" });
  }

  if (body.code !== "00") {
    // Thanh toán fail/hủy → đánh dấu đơn failed (chỉ khi còn pending).
    await restPatch(
      `topup_orders?provider_order_code=eq.${orderCode}&status=eq.pending`,
      { status: "failed", webhook_payload: body },
      fetchFn,
    );
    return json(200, { ok: true, applied: false });
  }

  const balance = (await rpc(
    "apply_topup",
    { p_order_code: orderCode, p_payload: body },
    fetchFn,
  )) as number;
  return json(200, { ok: true, applied: true, balanceCredits: balance });
}
