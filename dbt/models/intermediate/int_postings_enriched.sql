with postings as (

    select * from {{ ref('stg_job_postings') }}

),

skill_tags as (

    select
        posting_id,
        skills,
        array_size(skills) as skills_count,
        extracted_at
    from {{ source('raw', 'skill_tags') }}

),

enriched as (

    select
        p.*,

        -- Skills
        s.skills                                        as skills_array,
        coalesce(s.skills_count, 0)                     as skills_count,
        -- "has skills" means >=1 extracted skill, NOT merely "has a skill_tags row".
        -- A processed no-skill posting (a manager, a warehouse role) now has a row with an
        -- empty array so it stops re-exporting; `posting_id is not null` would count those
        -- as enriched-with-skills and inflate every skill-demand aggregate.
        coalesce(s.skills_count, 0) > 0                  as has_skills,
        array_to_string(s.skills, ', ')                 as skills_csv,
        -- Exposed so fct_postings can tell that enrichment arrived. Skills land DAYS
        -- after a posting is ingested (ingest -> export -> routine -> import), so a
        -- downstream incremental keyed only on loaded_at can never see them.
        s.extracted_at                                  as skills_extracted_at

    from postings p
    left join skill_tags s on p.posting_id = s.posting_id

)

select * from enriched
