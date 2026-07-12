with enriched as (

    select * from {{ ref('int_postings_enriched') }}

),

candidates as (

    select
        posting_id,
        source,
        title,
        company,
        url,
        location,
        country_code,
        role_category,
        is_remote,

        salary_raw,
        salary_min,
        salary_max,
        currency,

        skills_csv,
        skills_count,

        personal_score,
        personal_summary,

        posted_at,
        loaded_at,
        last_seen_at,
        notified,
        notified_at,

        -- Normalised dedup key: strip suffix after the first |/-/– separator and
        -- remove seniority words, so "Senior ML Engineer | Data" and "ML Engineer"
        -- at the same company collapse to one key.
        regexp_replace(
            regexp_replace(
                lower(split_part(split_part(split_part(title, '|', 1), ' - ', 1), ' – ', 1)),
                '\\b(senior|junior|medior|mid|mid-level|lead|principal|staff|sr|jr)\\b\\.?',
                ''
            ),
            '\\s+', ' '
        ) || '::' || lower(trim(coalesce(company, '')))          as dedup_key

    from enriched
    where personal_score >= {{ var('personal_score_threshold', 5) }}
      and role_category in ('data_engineering', 'data_analysis', 'machine_learning')
      -- Freshness: drop postings that have not re-appeared in a source feed
      -- recently (filled/expired jobs). last_seen_at is bumped on every re-ingest.
      and coalesce(last_seen_at, loaded_at)
          >= dateadd(day, -{{ var('match_freshness_days', 7) }}, current_timestamp())

),

deduped as (

    -- Suppress the whole dedup group if ANY member was already notified — otherwise
    -- notify.py (which only marks the winning posting_id) would re-send a losing
    -- duplicate next run. Then keep one representative row per group.
    select
        *,
        max(case when notified then 1 else 0 end)
            over (partition by dedup_key)                        as group_notified
    from candidates
    qualify row_number() over (
        partition by dedup_key
        order by personal_score desc, posted_at desc, posting_id
    ) = 1

)

select *
from deduped
where group_notified = 0
order by personal_score desc, posted_at desc
