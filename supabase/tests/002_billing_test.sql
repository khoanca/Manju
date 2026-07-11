-- pgTAP test cho 002_billing.sql — chạy: supabase test db
-- Giả lập user qua role authenticated + request.jwt.claims (chuẩn Supabase RLS test).
-- Race 2 session song song không test được trong 1 transaction pgTAP — an toàn
-- race dựa vào UPDATE single-statement trong spend_credits (row lock Postgres).

begin;
select plan(17);

-- ── Fixtures: 2 user + số dư ban đầu ─────────────────────────────────────────
insert into auth.users (id, email) values
  ('00000000-0000-0000-0000-00000000000a', 'a@test.local'),
  ('00000000-0000-0000-0000-00000000000b', 'b@test.local');

insert into wallets (user_id, balance_credits)
  values ('00000000-0000-0000-0000-00000000000a', 10000);  -- 10 credit

insert into topup_orders (user_id, provider_order_code, package_code, amount_vnd, credits)
  values ('00000000-0000-0000-0000-00000000000a', 111222, 'p50k', 50000, 50000000);

-- ── RLS: đọc ─────────────────────────────────────────────────────────────────
set local role authenticated;
set local "request.jwt.claims" = '{"sub":"00000000-0000-0000-0000-00000000000a"}';

select is(
  (select count(*) from wallets), 1::bigint,
  'user A thấy đúng 1 ví (của mình)');

select is(
  (select count(*) from topup_orders), 1::bigint,
  'user A thấy đơn nạp của mình');

select ok(
  (select count(*) from pricing_rates where model = 'anthropic/claude-haiku-4.5') = 1,
  'user đăng nhập đọc được bảng giá active');

select ok(
  (select count(*) from topup_packages) >= 1,
  'user đăng nhập đọc được gói nạp active');

set local "request.jwt.claims" = '{"sub":"00000000-0000-0000-0000-00000000000b"}';

select is(
  (select count(*) from wallets), 0::bigint,
  'user B không thấy ví của A');

select is(
  (select count(*) from topup_orders), 0::bigint,
  'user B không thấy đơn nạp của A');

-- ── RLS: ghi trực tiếp bị chặn (không có policy ghi) ─────────────────────────
select throws_ok(
  $$ insert into wallets (user_id, balance_credits)
     values ('00000000-0000-0000-0000-00000000000b', 999999) $$,
  '42501', null,
  'authenticated không INSERT được wallets');

-- UPDATE không có policy → không lỗi nhưng 0 hàng bị sửa (RLS lọc hết).
set local "request.jwt.claims" = '{"sub":"00000000-0000-0000-0000-00000000000a"}';
select lives_ok(
  $$ update wallets set balance_credits = 999999 $$,
  'UPDATE wallets không lỗi (RLS lọc, không match hàng nào)');
select is(
  (select balance_credits from wallets
    where user_id = '00000000-0000-0000-0000-00000000000a'),
  10000::bigint,
  'số dư KHÔNG đổi sau UPDATE trực tiếp — không có policy ghi');

select throws_ok(
  $$ select spend_credits('00000000-0000-0000-0000-00000000000a', 1, gen_random_uuid(), '{}') $$,
  '42501', null,
  'authenticated không gọi được spend_credits (revoked)');

select throws_ok(
  $$ select apply_topup(111222, '{}') $$,
  '42501', null,
  'authenticated không gọi được apply_topup (revoked)');

-- ── RPC spend_credits (chạy như service: postgres) ───────────────────────────
reset role;
reset "request.jwt.claims";

select is(
  spend_credits('00000000-0000-0000-0000-00000000000a', 3000,
                '11111111-1111-1111-1111-111111111111',
                '{"model":"m","prompt_tokens":100,"completion_tokens":50}'::jsonb),
  7000::bigint,
  'spend_credits trừ đúng: 10000 - 3000 = 7000');

select is(
  spend_credits('00000000-0000-0000-0000-00000000000a', 3000,
                '11111111-1111-1111-1111-111111111111', '{}'::jsonb),
  7000::bigint,
  'retry cùng request_id → idempotent, không trừ đôi');

select is(
  spend_credits('00000000-0000-0000-0000-00000000000a', 99999,
                '22222222-2222-2222-2222-222222222222', '{}'::jsonb),
  0::bigint,
  'usage vượt số dư → clamp về 0, không âm ví');

select is(
  (select count(*) from credit_ledger
    where user_id = '00000000-0000-0000-0000-00000000000a'
      and reason = 'llm_correct'),
  2::bigint,
  'ledger ghi 2 lần trừ (retry không thêm dòng)');

-- ── RPC apply_topup ──────────────────────────────────────────────────────────
select is(
  apply_topup(111222, '{"src":"payos"}'::jsonb),
  50000000::bigint,
  'apply_topup: pending → paid, cộng 50M vào ví (đang 0)');

select is(
  apply_topup(111222, '{"src":"payos-retry"}'::jsonb),
  50000000::bigint,
  'webhook trùng → no-op, số dư giữ nguyên');

select * from finish();
rollback;
