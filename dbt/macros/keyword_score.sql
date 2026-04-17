{% macro keyword_score(description, title) %}
    (
        case when lower({{ title }}) like '%data engineer%' then 3
             when lower({{ title }}) like '%analytics engineer%' then 3
             when lower({{ title }}) like '%data analyst%' then 2
             when lower({{ title }}) like '%bi analyst%' then 2
             when lower({{ title }}) like '%ml engineer%' then 1
             when lower({{ title }}) like '%data scientist%' then 1
             else 0
        end
        + case when lower({{ description }}) like '%dbt%' then 2 else 0 end
        + case when lower({{ description }}) like '%snowflake%' then 2 else 0 end
        + case when lower({{ description }}) like '%airflow%' then 1 else 0 end
        + case when lower({{ description }}) like '%python%' then 1 else 0 end
        + case when lower({{ description }}) like '%sql%' then 1 else 0 end
    )
{% endmacro %}
