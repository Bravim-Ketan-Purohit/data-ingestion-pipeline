-- Quality gate test: every field must have a valid document reference
-- A failing test blocks publish to Gold

SELECT
    field_id,
    document_id
FROM {{ ref('stg_silver_fields') }}
WHERE document_id IS NULL OR document_id = ''
