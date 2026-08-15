-- Quality gate test: confidence scores must be between 0 and 1
-- A failing dbt test BLOCKS publish to Gold (SPEC §15.5)

SELECT
    field_id,
    confidence
FROM {{ ref('stg_silver_fields') }}
WHERE confidence < 0.0 OR confidence > 1.0
