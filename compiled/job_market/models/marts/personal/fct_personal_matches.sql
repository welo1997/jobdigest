with enriched as (

    select * from JOB_MARKET.raw_intermediate.int_postings_enriched

),

matches as (

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
        notified,
        notified_at

    from enriched
    where personal_score >= 7
      and notified = false
      and is_remote = true
      and role_category in ('data_engineering', 'data_analysis', 'machine_learning')

)

select *
from matches
order by personal_score desc, posted_at desc