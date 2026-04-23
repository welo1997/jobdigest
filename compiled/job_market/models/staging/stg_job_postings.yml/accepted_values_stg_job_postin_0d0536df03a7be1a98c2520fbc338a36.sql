
    
    

with all_values as (

    select
        source as value_field,
        count(*) as n_records

    from JOB_MARKET.raw_staging.stg_job_postings
    group by source

)

select *
from all_values
where value_field not in (
    'remotive','adzuna','weworkremotely','startupjobs','greenhouse','lever','jobscz','profesia','linkedin','eurojobs','cocuma'
)


