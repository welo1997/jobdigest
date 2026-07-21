{{
    config(
        materialized='incremental',
        unique_key='posting_id',
        incremental_strategy='merge',
        on_schema_change='append_new_columns'
    )
}}

with enriched as (

    select * from {{ ref('int_postings_enriched') }}
    {% if is_incremental() %}
    where loaded_at > (select coalesce(max(loaded_at), '1900-01-01') from {{ this }})
       -- Enrichment arrives DAYS after ingestion -- that is the design, not a lag to fix:
       -- ingest -> export postings.json -> claude.ai routine -> import. By the time skills
       -- exist, the posting's loaded_at sits far below the watermark, so a loaded_at-only
       -- predicate would never revisit it and has_skills/skills_csv would read false
       -- forever. Verified on 2026-07-21: 89 postings had skills in
       -- int_postings_enriched and 0 in this table.
       -- Re-select anything enriched since the last build. `merge` on posting_id updates
       -- those rows in place rather than duplicating them.
       or skills_extracted_at >
          (select coalesce(max(skills_extracted_at), '1900-01-01') from {{ this }})
    {% endif %}
    qualify row_number() over (partition by posting_id order by loaded_at desc) = 1

)

select
    posting_id,
    source,
    title,
    company,
    url,
    location,
    country_code,
    is_remote,
    role_category,
    is_tech_company,

    -- Salary
    salary_raw,
    salary_min,
    salary_max,
    currency,

    -- Skills
    has_skills,
    skills_count,
    skills_csv,
    -- Must be selected, not just filtered on: the incremental predicate above reads
    -- max(skills_extracted_at) from this table, so the column has to exist here.
    skills_extracted_at,

    -- Time dimensions
    posted_at,
    loaded_at,
    date_trunc('week', coalesce(posted_at, loaded_at::date))    as posting_week,
    date_trunc('month', coalesce(posted_at, loaded_at::date))   as posting_month

from enriched
