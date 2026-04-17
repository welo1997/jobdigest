{% macro parse_salary_min(salary_raw) %}
    case
        when {{ salary_raw }} is null then null
        when {{ salary_raw }} like '%-%'
            then try_to_number(
                trim(regexp_replace(split_part({{ salary_raw }}, '-', 1), '[^0-9.]', '')),
                38, 2
            )
        else try_to_number(
            trim(regexp_replace({{ salary_raw }}, '[^0-9.]', '')),
            38, 2
        )
    end
{% endmacro %}

{% macro parse_salary_max(salary_raw) %}
    case
        when {{ salary_raw }} is null then null
        when {{ salary_raw }} like '%-%'
            then try_to_number(
                trim(regexp_replace(split_part({{ salary_raw }}, '-', 2), '[^0-9.]', '')),
                38, 2
            )
        else null
    end
{% endmacro %}
