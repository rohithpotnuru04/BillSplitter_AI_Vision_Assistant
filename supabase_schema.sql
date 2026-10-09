
create extension if not exists pgcrypto;

create table if not exists public.bill_drafts (
  id uuid primary key default gen_random_uuid(),
  owner_email text not null,
  owner_name text not null,
  title text not null default 'New Bill',
  status text not null default 'draft',
  state jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create index if not exists bill_drafts_owner_email_idx
  on public.bill_drafts(owner_email);

-- The Streamlit server should use SUPABASE_SERVICE_ROLE_KEY.
-- Service-role access bypasses RLS server-side, so users never receive
-- database credentials in the browser.
alter table public.bill_drafts enable row level security;

-- No public/anon policies are intentionally added.
-- If you use an anon key instead, you must design RLS policies carefully.


-- Optional: if you later want to persist receipt image files separately,
-- create a private storage bucket named bill-receipts and store its object path
-- in the bill_drafts state. The current version keeps the structured receipt
-- data in the draft so the bill can be resumed immediately.
