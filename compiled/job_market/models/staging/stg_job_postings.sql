with source as (

    select * from JOB_MARKET.RAW.job_postings

),

staged as (

    select
        posting_id,
        source,
        trim(title)                                     as title,
        trim(company)                                   as company,
        url,
        description,
        trim(location)                                  as location,
        upper(trim(country_code))                       as country_code,

        -- Remote flag: trust ingestor's TRUE; otherwise scan title/location/description
        -- for explicit remote keywords. Catches ATS sources (greenhouse/lever) that
        -- expose location strings like "Remote - US" or "Germany (Remote)".
        case
            when remote_signal = true then true
            when lower(coalesce(location, '')) like '%remote%'
              or lower(coalesce(location, '')) like '%anywhere%' then true
            when lower(coalesce(title, '')) like '%(remote)%'
              or lower(coalesce(title, '')) like '%remote)%'
              or lower(coalesce(title, '')) like '% remote %'
              or lower(coalesce(title, '')) like 'remote %' then true
            when regexp_like(
                lower(coalesce(description, '')),
                '(fully remote|100% remote|remote[- ]first|remote[- ]friendly|work from anywhere|work[- ]from[- ]home|home[- ]office|z domova|this is a remote|remote position|remote role)'
            ) then true
            else false
        end                                             as is_remote,

        -- Role category based on title keywords
        case
            when lower(title) like any ('%data engineer%', '%analytics engineer%', '%dataops%', '%etl%')
                then 'data_engineering'
            when lower(title) like any ('%data analyst%', '%bi analyst%', '%business intelligence%')
                then 'data_analysis'
            when lower(title) like any ('%machine learning%', '%ml engineer%', '%ai engineer%', '%data scientist%')
                then 'machine_learning'
            when lower(title) like any ('%software engineer%', '%backend%', '%frontend%', '%fullstack%', '%full-stack%', '%full stack%', '%mobile developer%', '%web developer%', '%software developer%')
                then 'software_engineering'
            when lower(title) like any ('%devops%', '%platform engineer%', '%sre%', '%site reliability%', '%cloud engineer%', '%infrastructure%')
                then 'devops_platform'
            when lower(title) like any ('%product manager%', '%product owner%', '%program manager%', '%tpm%')
                then 'product'
            when lower(title) like any (
                '%ux designer%', '%ui designer%', '%ux/ui%', '%ui/ux%',
                '%product designer%', '%visual designer%', '%interaction designer%',
                '%graphic designer%', '%motion designer%', '%webdesigner%',
                '%web designer%', '%ux researcher%', '%ux concepter%',
                '%user experience%', '%user interface designer%',
                '%digital designer%', '%content designer%'
            )
                then 'design'
            when lower(title) like any ('%marketing%', '%sales%', '%finance%', '%accounting%', '%recruiter%', '%hr %', '%human resources%', '%legal%', '%operations%')
                then 'other_tech_function'
            else 'uncategorised'
        end                                             as role_category,

        -- Salary parsing
        salary_raw,
        
    case
        when salary_raw is null then null
        when salary_raw like '%-%'
            then try_to_number(
                trim(regexp_replace(split_part(salary_raw, '-', 1), '[^0-9.]', '')),
                38, 2
            )
        else try_to_number(
            trim(regexp_replace(salary_raw, '[^0-9.]', '')),
            38, 2
        )
    end
            as salary_min,
        
    case
        when salary_raw is null then null
        when salary_raw like '%-%'
            then try_to_number(
                trim(regexp_replace(split_part(salary_raw, '-', 2), '[^0-9.]', '')),
                38, 2
            )
        else null
    end
            as salary_max,
        currency,

        -- Tech company flag: inherently tech sources or in taxonomy seed
        case
            when source in ('remotive', 'weworkremotely', 'startupjobs', 'greenhouse')
                then true
            else false
        end                                             as is_tech_company,

        posted_at,
        loaded_at,
        notified,
        notified_at

    from source

)

select * from staged