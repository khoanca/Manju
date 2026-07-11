-- Đợt 2b — ví credit (PRD FR-6 / BRD YC-6). Chạy trên Supabase (Postgres),
-- SAU 001_org.sql (dùng chung auth.users).
--
-- Nguyên tắc:
--   - Client (JWT user) chỉ ĐỌC dữ liệu của mình qua RLS; KHÔNG có policy
--     insert/update/delete → mọi mutation đi qua RPC SECURITY DEFINER do
--     Edge Function (service_role) gọi. Không tin client (như FR-5).
--   - Credit lưu bigint ×1000 (milli-credit): 1000 = 1 credit. Tránh float
--     drift khi trừ per-sentence trong live.
--   - Markup nằm trong pricing_rates (owner đặt giá bán, không lộ giá gốc).
--   - Rollback: rollback/002_billing_down.sql (chạy tay, KHÔNG auto-run;
--     pg_dump 3 bảng billing trước khi chạy).

-- ── Bảng ────────────────────────────────────────────────────────────────────
do $$ begin
  create type ledger_reason as enum ('llm_correct', 'topup', 'adjustment', 'refund');
exception when duplicate_object then null; end $$;

create table if not exists wallets (
  user_id         uuid primary key references auth.users(id) on delete cascade,
  balance_credits bigint not null default 0 check (balance_credits >= 0),
  updated_at      timestamptz not null default now()
);

-- Sổ cái append-only — nguồn chân lý cho mọi biến động ví.
-- delta_credits: chi phí THẬT (âm khi trừ), có thể vượt số dư còn lại (overshoot
-- ≤1 call do pre-flight là estimate) — ví clamp về 0, balance_after ghi sau clamp.
create table if not exists credit_ledger (
  id                bigint generated always as identity primary key,
  user_id           uuid not null references auth.users(id) on delete cascade,
  delta_credits     bigint not null,
  balance_after     bigint not null,
  reason            ledger_reason not null,
  request_id        uuid,   -- idempotency cho lần trừ (retry không trừ đôi)
  order_id          uuid,   -- FK topup_orders cho lần nạp
  model             text,
  prompt_tokens     integer,
  completion_tokens integer,
  created_at        timestamptz not null default now()
);
create unique index if not exists uq_ledger_request
  on credit_ledger (request_id) where request_id is not null;
create index if not exists idx_ledger_user on credit_ledger (user_id, created_at desc);

-- Giá bán (markup nướng sẵn), đơn vị milli-credit trên 1k token.
create table if not exists pricing_rates (
  model                     text primary key,
  credits_per_1k_prompt     bigint not null check (credits_per_1k_prompt >= 0),
  credits_per_1k_completion bigint not null check (credits_per_1k_completion >= 0),
  active                    boolean not null default true,
  updated_at                timestamptz not null default now()
);

-- Gói nạp VND → credit, owner cấu hình.
create table if not exists topup_packages (
  code       text primary key,
  amount_vnd bigint not null check (amount_vnd > 0),
  credits    bigint not null check (credits > 0),
  active     boolean not null default true
);

create table if not exists topup_orders (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid not null references auth.users(id) on delete cascade,
  provider            text not null default 'payos' check (provider in ('payos', 'sepay')),
  provider_order_code bigint not null unique,  -- PayOS orderCode — mỏ neo idempotency
  package_code        text not null references topup_packages(code),
  amount_vnd          bigint not null,
  credits             bigint not null,
  status              text not null default 'pending'
                      check (status in ('pending', 'paid', 'failed', 'expired')),
  webhook_payload     jsonb,
  created_at          timestamptz not null default now(),
  paid_at             timestamptz
);
create index if not exists idx_orders_user on topup_orders (user_id, created_at desc);

-- ── RPC (chỉ service_role gọi — revoke bên dưới) ────────────────────────────
-- Trừ ví atomic, race-safe, idempotent theo p_request_id.
--   p_usage: {"model": text, "prompt_tokens": int, "completion_tokens": int}
-- Trả về balance_after (milli-credit). Clamp về 0 khi usage thật vượt số dư
-- (pre-flight ở llm-correct đã chặn trước khi gọi LLM; overshoot ≤1 call).
create or replace function spend_credits(
  p_user_id uuid, p_credits bigint, p_request_id uuid, p_usage jsonb
) returns bigint language plpgsql security definer set search_path = public as $$
declare
  v_balance bigint;
begin
  if p_credits < 0 then
    raise exception 'spend_credits: p_credits must be >= 0';
  end if;

  select balance_after into v_balance
    from credit_ledger where request_id = p_request_id;
  if found then
    return v_balance;  -- retry cùng request_id → không trừ đôi
  end if;

  insert into wallets (user_id) values (p_user_id)
    on conflict (user_id) do nothing;  -- ví tạo lazy

  update wallets
     set balance_credits = greatest(balance_credits - p_credits, 0),
         updated_at = now()
   where user_id = p_user_id
   returning balance_credits into v_balance;

  insert into credit_ledger
    (user_id, delta_credits, balance_after, reason, request_id,
     model, prompt_tokens, completion_tokens)
  values
    (p_user_id, -p_credits, v_balance, 'llm_correct', p_request_id,
     p_usage->>'model', (p_usage->>'prompt_tokens')::integer,
     (p_usage->>'completion_tokens')::integer);

  return v_balance;
end;
$$;

-- Cộng ví khi webhook xác nhận. Idempotent: chỉ đơn đang 'pending' được chuyển
-- 'paid'; webhook trùng/đơn lạ → no-op, trả số dư hiện tại (webhook luôn 200).
create or replace function apply_topup(
  p_order_code bigint, p_payload jsonb
) returns bigint language plpgsql security definer set search_path = public as $$
declare
  v_order topup_orders%rowtype;
  v_balance bigint;
begin
  update topup_orders
     set status = 'paid', paid_at = now(), webhook_payload = p_payload
   where provider_order_code = p_order_code and status = 'pending'
   returning * into v_order;

  if not found then  -- đã xử lý rồi hoặc orderCode lạ
    select coalesce(
      (select w.balance_credits from topup_orders o
         join wallets w on w.user_id = o.user_id
        where o.provider_order_code = p_order_code), 0)
      into v_balance;
    return v_balance;
  end if;

  insert into wallets (user_id, balance_credits)
    values (v_order.user_id, v_order.credits)
    on conflict (user_id) do update
      set balance_credits = wallets.balance_credits + excluded.balance_credits,
          updated_at = now();

  select balance_credits into v_balance
    from wallets where user_id = v_order.user_id;

  insert into credit_ledger
    (user_id, delta_credits, balance_after, reason, order_id)
  values
    (v_order.user_id, v_order.credits, v_balance, 'topup', v_order.id);

  return v_balance;
end;
$$;

-- Revoke PUBLIC xóa luôn grant mặc định → service_role cũng mất quyền nếu
-- không grant lại tường minh (Edge Function gọi RPC bằng service_role).
revoke execute on function spend_credits(uuid, bigint, uuid, jsonb)
  from public, anon, authenticated;
revoke execute on function apply_topup(bigint, jsonb)
  from public, anon, authenticated;
grant execute on function spend_credits(uuid, bigint, uuid, jsonb) to service_role;
grant execute on function apply_topup(bigint, jsonb) to service_role;

-- ── RLS ─────────────────────────────────────────────────────────────────────
alter table wallets enable row level security;
alter table credit_ledger enable row level security;
alter table topup_orders enable row level security;
alter table pricing_rates enable row level security;
alter table topup_packages enable row level security;

-- Chỉ SELECT dữ liệu của mình; không policy ghi → chỉ RPC/service_role ghi.
create policy w_select on wallets
  for select using (user_id = auth.uid());
create policy cl_select on credit_ledger
  for select using (user_id = auth.uid());
create policy to_select on topup_orders
  for select using (user_id = auth.uid());

-- Bảng giá/gói: user đăng nhập đọc được hàng đang active.
create policy pr_select on pricing_rates
  for select to authenticated using (active);
create policy tp_select on topup_packages
  for select to authenticated using (active);

-- ── Seed PLACEHOLDER — owner PHẢI đặt giá thật trước khi launch ─────────────
insert into pricing_rates (model, credits_per_1k_prompt, credits_per_1k_completion)
values ('anthropic/claude-haiku-4.5', 1000, 5000)
on conflict (model) do nothing;

insert into topup_packages (code, amount_vnd, credits) values
  ('p50k',  50000,  50000000),   -- 50k VND  = 50k credit (PLACEHOLDER)
  ('p100k', 100000, 110000000),  -- 100k VND = 110k credit (PLACEHOLDER)
  ('p200k', 200000, 240000000)   -- 200k VND = 240k credit (PLACEHOLDER)
on conflict (code) do nothing;
