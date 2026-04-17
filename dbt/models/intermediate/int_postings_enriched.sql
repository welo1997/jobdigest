with postings as (

    select * from {{ ref('stg_job_postings') }}

),

skill_tags as (

    select
        posting_id,
        skills,
        array_size(skills) as skills_count
    from {{ source('raw', 'skill_tags') }}

),

personal_scores as (

    select
        posting_id,
        personal_score,
        summary as personal_summary,
        scored_at
    from {{ source('raw', 'personal_scores') }}

),

enriched as (

    select
        p.*,

        -- Skills
        s.skills                                        as skills_array,
        coalesce(s.skills_count, 0)                     as skills_count,
        s.posting_id is not null                        as has_skills,
        array_to_string(s.skills, ', ')                 as skills_csv,

        -- Personal scoring
        ps.personal_score,
        ps.personal_summary,
        ps.scored_at                                    as personal_scored_at,
        ps.posting_id is not null                       as has_personal_score

    from postings p
    left join skill_tags s on p.posting_id = s.posting_id
    left join personal_scores ps on p.posting_id = ps.posting_id

)

select * from enriched
