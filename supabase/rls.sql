-- Run after supabase/schema.sql.

begin;

create schema if not exists private;

create or replace function private.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.profiles p
        where p.auth_user_id = (select auth.uid())
          and p.approved
          and p.can_view
          and p.is_admin
    );
$$;

create or replace function private.can_view()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.profiles p
        where p.auth_user_id = (select auth.uid())
          and p.approved
          and p.can_view
    );
$$;

create or replace function private.can_download()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.profiles p
        where p.auth_user_id = (select auth.uid())
          and p.approved
          and p.can_view
          and p.can_download
    );
$$;

revoke all on schema private from public, anon;
revoke all on all functions in schema private from public, anon;
grant usage on schema private to authenticated;
grant execute on function private.is_admin() to authenticated;
grant execute on function private.can_view() to authenticated;
grant execute on function private.can_download() to authenticated;

alter table public.profiles enable row level security;
alter table public.email_messages enable row level security;
alter table public.runs enable row level security;
alter table public.logs enable row level security;

revoke all on table public.profiles from anon, authenticated;
revoke all on table public.email_messages from anon, authenticated;
revoke all on table public.runs from anon, authenticated;
revoke all on table public.logs from anon, authenticated;

revoke all on function public.save_run_with_logs(jsonb, jsonb) from public, anon;
revoke all on function public.replace_email_runs_with_logs(bigint, jsonb)
    from public, anon, authenticated;
revoke all on function public.dashboard_metrics(
    timestamptz, timestamptz, text, text, text, text, integer
) from public, anon;
revoke all on function public.data_quality_metrics() from public, anon;

grant select on table public.profiles to authenticated;
grant update (display_name, approved, can_view, can_download, updated_at)
    on table public.profiles to authenticated;
grant select, insert on table public.runs to authenticated;
grant select, insert on table public.logs to authenticated;
grant select on table public.email_messages to authenticated;
grant usage, select on all sequences in schema public to authenticated;
grant all on table public.profiles, public.email_messages, public.runs, public.logs
    to service_role;
grant usage, select on all sequences in schema public to service_role;
grant execute on function public.save_run_with_logs(jsonb, jsonb)
    to authenticated, service_role;
grant execute on function public.replace_email_runs_with_logs(bigint, jsonb)
    to service_role;
grant execute on function public.dashboard_metrics(
    timestamptz, timestamptz, text, text, text, text, integer
) to authenticated;
grant execute on function public.data_quality_metrics() to authenticated;

-- These tables belong to this application. Remove permissive prototype
-- policies so PostgreSQL's OR-combination cannot bypass the rules below.
do $$
declare
    policy_row record;
begin
    for policy_row in
        select schemaname, tablename, policyname
        from pg_policies
        where schemaname = 'public'
          and tablename in ('profiles', 'email_messages', 'runs', 'logs')
    loop
        execute format(
            'drop policy if exists %I on %I.%I',
            policy_row.policyname,
            policy_row.schemaname,
            policy_row.tablename
        );
    end loop;
end $$;

drop policy if exists "profile owner or admin can read" on public.profiles;
create policy "profile owner or admin can read"
on public.profiles for select to authenticated
using (
    (select auth.uid()) is not null
    and (
        auth_user_id = (select auth.uid())
        or (select private.is_admin())
    )
);

drop policy if exists "admin can update profiles" on public.profiles;
create policy "admin can update profiles"
on public.profiles for update to authenticated
using ((select private.is_admin()))
with check ((select private.is_admin()));

drop policy if exists "approved viewers can read runs" on public.runs;
create policy "approved viewers can read runs"
on public.runs for select to authenticated
using ((select private.can_view()));

drop policy if exists "approved viewers can upload docx runs" on public.runs;
create policy "approved viewers can upload docx runs"
on public.runs for insert to authenticated
with check (
    (select private.can_view())
    and source = 'docx'
    and email_message_id is null
    and created_by = (select auth.uid())
    and sender_email = (select auth.jwt() ->> 'email')
);

drop policy if exists "approved viewers can read logs" on public.logs;
create policy "approved viewers can read logs"
on public.logs for select to authenticated
using ((select private.can_view()));

drop policy if exists "run owners can insert logs" on public.logs;
create policy "run owners can insert logs"
on public.logs for insert to authenticated
with check (
    (select private.can_view())
    and exists (
        select 1 from public.runs r
        where r.id = run_id
          and r.created_by = (select auth.uid())
    )
);

drop policy if exists "admins can read email quality data" on public.email_messages;
create policy "admins can read email quality data"
on public.email_messages for select to authenticated
using ((select private.is_admin()));

commit;
