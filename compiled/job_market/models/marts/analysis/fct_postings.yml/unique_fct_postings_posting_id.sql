
    
    

select
    posting_id as unique_field,
    count(*) as n_records

from JOB_MARKET.raw_marts_analysis.fct_postings
where posting_id is not null
group by posting_id
having count(*) > 1


