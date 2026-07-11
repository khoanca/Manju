// Tạo đơn nạp credit (US-604): user chọn gói → tạo topup_orders (pending) →
// gọi PayOS payment-requests (ký HMAC) → trả checkoutUrl/qrCode cho client.
// Client theo dõi trạng thái đơn bằng cách đọc topup_orders qua PostgREST
// (RLS select của mình) — không cần function poll riêng.

import {
  env,
  type FetchFn,
  json,
  jwtSub,
  payosSignature,
  restGet,
  restPatch,
  restPost,
} from "../_shared/billing.ts";

const PAYOS_API = "https://api-merchant.payos.vn";

interface OrderBody {
  packageCode?: string;
}

interface TopupPackage {
  code: string;
  amount_vnd: number;
  credits: number;
}

interface OrderRow {
  id: string;
  provider_order_code: number;
}

async function createPayosRequest(
  order: { orderCode: number; amountVnd: number; packageCode: string },
  fetchFn: FetchFn,
): Promise<Response> {
  const description = `Manju credit ${order.packageCode}`;
  const cancelUrl = Deno.env.get("PAYOS_CANCEL_URL") ?? "https://payos.vn";
  const returnUrl = Deno.env.get("PAYOS_RETURN_URL") ?? "https://payos.vn";
  const signature = await payosSignature(
    {
      amount: order.amountVnd,
      cancelUrl,
      description,
      orderCode: order.orderCode,
      returnUrl,
    },
    env("PAYOS_CHECKSUM_KEY"),
  );
  return await fetchFn(`${PAYOS_API}/v2/payment-requests`, {
    method: "POST",
    headers: {
      "x-client-id": env("PAYOS_CLIENT_ID"),
      "x-api-key": env("PAYOS_API_KEY"),
      "content-type": "application/json",
    },
    body: JSON.stringify({
      orderCode: order.orderCode,
      amount: order.amountVnd,
      description,
      cancelUrl,
      returnUrl,
      signature,
    }),
  });
}

export async function handlePayosOrder(
  req: Request,
  fetchFn: FetchFn = fetch,
): Promise<Response> {
  if (req.method !== "POST") return json(405, { error: "method_not_allowed" });
  const sub = jwtSub(req);
  if (!sub) return json(401, { error: "unauthorized" });

  let body: OrderBody;
  try {
    body = (await req.json()) as OrderBody;
  } catch {
    return json(400, { error: "bad_json" });
  }
  if (!body.packageCode) return json(400, { error: "missing_package_code" });

  const packages = (await restGet(
    `topup_packages?code=eq.${encodeURIComponent(body.packageCode)}&active=is.true`,
    fetchFn,
  )) as TopupPackage[];
  if (!packages.length) return json(404, { error: "unknown_package" });
  const pkg = packages[0];

  // orderCode PayOS: số nguyên unique — epoch-ms ×1000 + ngẫu nhiên 3 chữ số;
  // unique constraint trong DB đỡ va chạm còn lại.
  const orderCode = Date.now() * 1000 + Math.floor(Math.random() * 1000);

  const rows = (await restPost(
    "topup_orders",
    {
      user_id: sub,
      provider: "payos",
      provider_order_code: orderCode,
      package_code: pkg.code,
      amount_vnd: pkg.amount_vnd,
      credits: pkg.credits,
    },
    fetchFn,
  )) as OrderRow[];
  const order = rows[0];

  const payosResp = await createPayosRequest(
    { orderCode, amountVnd: pkg.amount_vnd, packageCode: pkg.code },
    fetchFn,
  );
  if (!payosResp.ok) {
    await restPatch(
      `topup_orders?id=eq.${order.id}&status=eq.pending`,
      { status: "failed" },
      fetchFn,
    );
    return json(502, { error: "payos_error", status: payosResp.status });
  }
  const payos = (await payosResp.json()) as {
    code?: string;
    data?: { checkoutUrl?: string; qrCode?: string };
  };
  if (payos.code !== "00" || !payos.data?.checkoutUrl) {
    await restPatch(
      `topup_orders?id=eq.${order.id}&status=eq.pending`,
      { status: "failed" },
      fetchFn,
    );
    return json(502, { error: "payos_rejected", code: payos.code });
  }

  return json(200, {
    orderId: order.id,
    orderCode,
    checkoutUrl: payos.data.checkoutUrl,
    qrCode: payos.data.qrCode ?? null,
    amountVnd: pkg.amount_vnd,
    credits: pkg.credits, // milli-credit
  });
}
