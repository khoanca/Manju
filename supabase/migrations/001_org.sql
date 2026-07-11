-- Đợt 2 — org cloud: schema TEXT-only + RLS. Chạy trên Supabase (Postgres).
-- Local app đẩy TEXT (không audio) lên đây; quyền xem thực thi bằng RLS ngay
-- trong Postgres — KHÔNG tin client (PRD FR-5).
--
-- Ma trận quyền xem transcripts_text:
--   Member                       → bản của chính mình
--   Member có visibility_grant   → + toàn bộ bản đã push của người được grant
--   Admin                        → tất cả bản trong org; quản lý member + grants
--
-- Bootstrap: admin/member đầu tiên phải được thêm qua service_role (Edge
-- Function invite ở bước sau) vì RLS ghi org_members chỉ cho admin — lúc đầu
-- chưa có admin nào.

create extension if not exists pgcrypto;

-- ── Bảng ────────────────────────────────────────────────────────────────────
create table if not exists orgs (
  id         uuid primary key default gen_random_uuid(),
  name       text not null,
  created_at timestamptz not null default now()
);

create table if not exists org_members (
  org_id     uuid not null references orgs(id) on delete cascade,
  user_id    uuid not null references auth.users(id) on delete cascade,
  role       text not null default 'member' check (role in ('member', 'admin')),
  created_at timestamptz not null default now(),
  primary key (org_id, user_id)
);

-- Ai (viewer) xem được text của ai (subject) ngoài chính mình. Admin cấp/thu.
create table if not exists visibility_grants (
  org_id     uuid not null references orgs(id) on delete cascade,
  viewer_id  uuid not null references auth.users(id) on delete cascade,
  subject_id uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (org_id, viewer_id, subject_id)
);

-- Bản text đẩy lên (không audio). local_id = id bản ghi trên máy user →
-- (org_id, owner_id, local_id) unique cho phép push lại = upsert.
-- owner_id mặc định auth.uid(): client không gửi, DB tự gán người đăng nhập.
create table if not exists transcripts_text (
  id         uuid primary key default gen_random_uuid(),
  org_id     uuid not null references orgs(id) on delete cascade,
  owner_id   uuid not null default auth.uid() references auth.users(id) on delete cascade,
  local_id   text not null,
  title      text not null,
  language   text not null,
  model      text,
  duration   real,
  text       text not null,
  raw_text   text,
  corrected  boolean not null default false,
  llm_model  text,
  created_at timestamptz not null default now(),
  pushed_at  timestamptz not null default now(),
  unique (org_id, owner_id, local_id)
);
create index if not exists idx_tt_org_owner on transcripts_text (org_id, owner_id);

-- ── Helper: check quyền KHÔNG đệ quy RLS ────────────────────────────────────
-- SECURITY DEFINER chạy với quyền chủ hàm → bỏ qua RLS của org_members, tránh
-- vòng lặp policy (policy transcripts_text query org_members lại kích RLS...).
create or replace function is_org_member(p_org uuid)
returns boolean language sql security definer stable set search_path = public as $$
  select exists (
    select 1 from org_members where org_id = p_org and user_id = auth.uid()
  );
$$;

create or replace function is_org_admin(p_org uuid)
returns boolean language sql security definer stable set search_path = public as $$
  select exists (
    select 1 from org_members
    where org_id = p_org and user_id = auth.uid() and role = 'admin'
  );
$$;

create or replace function can_view_subject(p_org uuid, p_subject uuid)
returns boolean language sql security definer stable set search_path = public as $$
  select p_subject = auth.uid()
      or is_org_admin(p_org)
      or exists (
        select 1 from visibility_grants
        where org_id = p_org and viewer_id = auth.uid() and subject_id = p_subject
      );
$$;

-- ── RLS ─────────────────────────────────────────────────────────────────────
alter table orgs enable row level security;
alter table org_members enable row level security;
alter table visibility_grants enable row level security;
alter table transcripts_text enable row level security;

-- orgs: thành viên xem được org mình thuộc về.
create policy orgs_select on orgs
  for select using (is_org_member(id));

-- org_members: xem hàng của chính mình; admin xem + quản lý cả org.
create policy om_select on org_members
  for select using (user_id = auth.uid() or is_org_admin(org_id));
create policy om_admin_write on org_members
  for all using (is_org_admin(org_id)) with check (is_org_admin(org_id));

-- visibility_grants: viewer thấy grant của mình; admin quản lý.
create policy vg_select on visibility_grants
  for select using (viewer_id = auth.uid() or is_org_admin(org_id));
create policy vg_admin_write on visibility_grants
  for all using (is_org_admin(org_id)) with check (is_org_admin(org_id));

-- transcripts_text: xem theo ma trận quyền; chỉ chủ bản được push (upsert) bản
-- của mình; xoá bởi chủ hoặc admin.
create policy tt_select on transcripts_text
  for select using (can_view_subject(org_id, owner_id));
create policy tt_owner_insert on transcripts_text
  for insert with check (owner_id = auth.uid() and is_org_member(org_id));
create policy tt_owner_update on transcripts_text
  for update using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy tt_delete on transcripts_text
  for delete using (owner_id = auth.uid() or is_org_admin(org_id));
