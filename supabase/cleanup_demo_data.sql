-- One-time cleanup for the 1,000 prototype rows visible as "가짜 데이터".
-- Gmail-ingested rows are never eligible because they have an email_message_id.

begin;

do $$
declare
    demo_count bigint;
begin
    select count(*)
    into demo_count
    from public.runs
    where email_message_id is null
      and coalesce(source, 'docx') = 'docx'
      and (
          btrim(coalesce(sender_name, '')) = '가짜 데이터'
          or btrim(coalesce(uploader, '')) = '가짜 데이터'
      );

    if demo_count <> 1000 then
        raise exception
            'Safety stop: expected exactly 1000 demo runs, found %',
            demo_count;
    end if;

    delete from public.runs
    where email_message_id is null
      and coalesce(source, 'docx') = 'docx'
      and (
          btrim(coalesce(sender_name, '')) = '가짜 데이터'
          or btrim(coalesce(uploader, '')) = '가짜 데이터'
      );
end $$;

commit;

select
    count(*) as remaining_runs,
    count(*) filter (where source = 'gmail') as gmail_runs
from public.runs;
