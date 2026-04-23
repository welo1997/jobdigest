

with skill_tags as (

    select
        posting_id,
        skills,
        extracted_at
    from JOB_MARKET.RAW.skill_tags
    
    where extracted_at > (select coalesce(max(extracted_at), '1900-01-01') from JOB_MARKET.raw_intermediate.int_skill_exploded)
    

),

exploded as (

    select
        s.posting_id,
        trim(lower(sk.value::string))                   as skill_raw,
        s.extracted_at
    from skill_tags s,
    lateral flatten(input => s.skills) sk

),

normalised as (

    select
        e.posting_id,
        e.skill_raw,
        coalesce(t.canonical_skill, e.skill_raw)        as skill,
        coalesce(t.skill_category, 'other')             as skill_category,
        e.extracted_at
    from exploded e
    left join JOB_MARKET.raw_raw.skill_taxonomy t
        on e.skill_raw = t.skill_variant

)

select * from normalised