
    
    

with all_values as (

    select
        role_category as value_field,
        count(*) as n_records

    from JOB_MARKET.raw_staging.stg_job_postings
    group by role_category

)

select *
from all_values
where value_field not in (
    'data_engineering','data_analysis','machine_learning','software_engineering','devops_platform','product','design','other_tech_function','uncategorised'
)


