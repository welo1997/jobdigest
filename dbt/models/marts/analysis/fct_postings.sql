{{
    config(
        materialized='incremental',
        unique_key='posting_id',
        incremental_strategy='merge'
    )
}}

with enriched as (

    select * from {{ ref('int_postings_enriched') }}
    {% if is_incremental() %}
    where loaded_at > (select coalesce(max(loaded_at), '1900-01-01') from {{ this }})
    {% endif %}

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

    -- Personal scoring
    has_personal_score,
    personal_score,
    personal_summary,

    -- Time dimensions
    posted_at,
    loaded_at,
    date_trunc('week', coalesce(posted_at, loaded_at::date))    as posting_week,
    date_trunc('month', coalesce(posted_at, loaded_at::date))   as posting_month,

    -- Notification
    notified,
    notified_at

from enriched
