-- Allow one Gmail message to contain multiple independent simulation runs.
-- Safe to run repeatedly after the original schema.sql.

begin;

alter table public.runs
    add column if not exists source_run_index integer default 1;

update public.runs
set source_run_index = 1
where source_run_index is null or source_run_index < 1;

alter table public.runs alter column source_run_index set default 1;
alter table public.runs alter column source_run_index set not null;

drop index if exists public.runs_email_message_id_unique_idx;
create unique index if not exists runs_email_message_source_run_unique_idx
    on public.runs(email_message_id, source_run_index)
    where email_message_id is not null;

create or replace function public.save_run_with_logs(
    run_payload jsonb,
    log_payload jsonb default '[]'::jsonb
)
returns bigint
language plpgsql
security invoker
set search_path = ''
as $$
declare
    new_run_id bigint;
    item jsonb;
begin
    if jsonb_typeof(run_payload) <> 'object' then
        raise exception 'run_payload must be a JSON object';
    end if;
    if jsonb_typeof(coalesce(log_payload, '[]'::jsonb)) <> 'array' then
        raise exception 'log_payload must be a JSON array';
    end if;

    insert into public.runs (
        email_message_id, source_run_index, source, created_by,
        sender_name, sender_email, steel_user_id, process_type, run_date,
        status, score, user_level, steel_grade, time_minutes, tapping_mass,
        tap_temperature, total_energy_kwh, energy_kwh_per_t, power_cost,
        scrap_cost, additions_cost, other_consumables_cost, total_cost,
        cost_per_tonne, uploader, file_name, data_json
    ) values (
        nullif(run_payload ->> 'email_message_id', '')::bigint,
        coalesce(nullif(run_payload ->> 'source_run_index', '')::integer, 1),
        coalesce(nullif(run_payload ->> 'source', ''), 'docx'),
        (select auth.uid()),
        run_payload ->> 'sender_name',
        run_payload ->> 'sender_email',
        run_payload ->> 'steel_user_id',
        run_payload ->> 'process_type',
        nullif(run_payload ->> 'run_date', '')::timestamptz,
        nullif(run_payload ->> 'status', '')::integer,
        nullif(run_payload ->> 'score', '')::double precision,
        run_payload ->> 'user_level',
        run_payload ->> 'steel_grade',
        nullif(run_payload ->> 'time_minutes', '')::double precision,
        nullif(run_payload ->> 'tapping_mass', '')::double precision,
        nullif(run_payload ->> 'tap_temperature', '')::double precision,
        nullif(run_payload ->> 'total_energy_kwh', '')::double precision,
        nullif(run_payload ->> 'energy_kwh_per_t', '')::double precision,
        nullif(run_payload ->> 'power_cost', '')::double precision,
        nullif(run_payload ->> 'scrap_cost', '')::double precision,
        nullif(run_payload ->> 'additions_cost', '')::double precision,
        nullif(run_payload ->> 'other_consumables_cost', '')::double precision,
        nullif(run_payload ->> 'total_cost', '')::double precision,
        nullif(run_payload ->> 'cost_per_tonne', '')::double precision,
        run_payload ->> 'uploader',
        run_payload ->> 'file_name',
        coalesce(run_payload -> 'data_json', '{}'::jsonb)
    ) returning id into new_run_id;

    for item in
        select value
        from jsonb_array_elements(coalesce(log_payload, '[]'::jsonb))
    loop
        insert into public.logs (
            run_id, log_no, time, event_time, event_seconds, event, category
        ) values (
            new_run_id,
            (item ->> 'log_no')::integer,
            coalesce(item ->> 'time', item ->> 'event_time'),
            coalesce(item ->> 'event_time', item ->> 'time'),
            nullif(item ->> 'event_seconds', '')::double precision,
            item ->> 'event',
            item ->> 'category'
        );
    end loop;

    return new_run_id;
end;
$$;

create or replace function public.replace_email_runs_with_logs(
    p_email_message_id bigint,
    run_entries jsonb
)
returns bigint[]
language plpgsql
security invoker
set search_path = ''
as $$
declare
    entry jsonb;
    run_payload jsonb;
    new_run_id bigint;
    saved_ids bigint[] := array[]::bigint[];
begin
    if p_email_message_id is null then
        raise exception 'p_email_message_id is required';
    end if;
    if jsonb_typeof(run_entries) <> 'array' then
        raise exception 'run_entries must be a JSON array';
    end if;
    if jsonb_array_length(run_entries) < 1 then
        raise exception 'run_entries must not be empty';
    end if;
    if not exists (
        select 1 from public.email_messages where id = p_email_message_id
    ) then
        raise exception 'email_message_id % does not exist', p_email_message_id;
    end if;

    delete from public.runs where email_message_id = p_email_message_id;

    for entry in select value from jsonb_array_elements(run_entries)
    loop
        run_payload := coalesce(entry -> 'run_payload', '{}'::jsonb)
            || jsonb_build_object('email_message_id', p_email_message_id);
        if coalesce(
            nullif(run_payload ->> 'source_run_index', '')::integer, 0
        ) < 1 then
            raise exception 'source_run_index must be at least 1';
        end if;
        new_run_id := public.save_run_with_logs(
            run_payload,
            coalesce(entry -> 'log_payload', '[]'::jsonb)
        );
        saved_ids := array_append(saved_ids, new_run_id);
    end loop;

    return saved_ids;
end;
$$;

revoke all on function public.save_run_with_logs(jsonb, jsonb)
    from public, anon;
grant execute on function public.save_run_with_logs(jsonb, jsonb)
    to authenticated, service_role;

revoke all on function public.replace_email_runs_with_logs(bigint, jsonb)
    from public, anon, authenticated;
grant execute on function public.replace_email_runs_with_logs(bigint, jsonb)
    to service_role;

commit;
