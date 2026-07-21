with source as (

    select * from {{ source('raw', 'job_postings') }}

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
                '.*(fully remote|100% remote|remote[- ]first|remote[- ]friendly|work from anywhere|work[- ]from[- ]home|home[- ]office|z domova|this is a remote|remote position|remote role).*',
                's'  -- REGEXP_LIKE is fully anchored; wrap in .*…* and let . span newlines
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

        -- Work region — coarse geography for analysis (international-first).
        -- Derived from country_code + free-text location; 'worldwide' = remote-anywhere.
        case
            when upper(coalesce(country_code, '')) = 'CZ'
              or regexp_like(lower(coalesce(location, '')), '.*(czech|praha|prague|brno|ostrava).*', 's')
                then 'cz'
            when regexp_like(lower(coalesce(location, '')), '.*(worldwide|anywhere|global|fully remote).*', 's')
                then 'worldwide'
            when upper(coalesce(country_code, '')) = 'GB'
              or regexp_like(lower(coalesce(location, '')), '.*(united kingdom|england|london).*', 's')
                then 'uk'
            when upper(coalesce(country_code, '')) = 'US'
              or regexp_like(lower(coalesce(location, '')), '.*(united states|remote us).*', 's')
                then 'us'
            when upper(coalesce(country_code, '')) in
                 ('DE','NL','FR','ES','PL','AT','IE','PT','SK','IT','BE','SE','DK','FI')
              or regexp_like(lower(coalesce(location, '')), '.*(europe|emea|germany|netherlands|poland|austria|spain|france|ireland).*', 's')
                then 'eu'
            else 'other'
        end                                             as work_region,

        -- Salary parsing
        salary_raw,
        {{ parse_salary_min('salary_raw') }}            as salary_min,
        {{ parse_salary_max('salary_raw') }}            as salary_max,
        currency,

        -- Tech company flag: inherently tech sources or in taxonomy seed
        case
            when source in ('remotive', 'weworkremotely', 'startupjobs', 'greenhouse')
                then true
            else false
        end                                             as is_tech_company,

        posted_at,
        loaded_at,
        last_seen_at

    from source

)

select * from staged
