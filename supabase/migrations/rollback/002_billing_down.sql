-- Rollback 002_billing.sql — CHẠY TAY, không nằm trong flow migration.
-- credit_ledger là dữ liệu tài chính: pg_dump trước khi chạy!
--   pg_dump "$DB_URL" -t wallets -t credit_ledger -t topup_orders > billing_backup.sql
-- Thứ tự ngược 002: policies → functions → tables → type.

drop policy if exists tp_select on topup_packages;
drop policy if exists pr_select on pricing_rates;
drop policy if exists to_select on topup_orders;
drop policy if exists cl_select on credit_ledger;
drop policy if exists w_select on wallets;

drop function if exists apply_topup(bigint, jsonb);
drop function if exists spend_credits(uuid, bigint, uuid, jsonb);

drop table if exists topup_orders;
drop table if exists topup_packages;
drop table if exists pricing_rates;
drop table if exists credit_ledger;
drop table if exists wallets;

drop type if exists ledger_reason;
