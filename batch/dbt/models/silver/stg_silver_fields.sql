-- Staging model: Silver fields
-- Source: Delta Lake Silver layer (loaded via external table or spark write)

{{ config(materialized='view') }}

SELECT
    field_id,
    document_id,
    content_hash,
    field_path,
    field_value,
    confidence,
    source_partition_id,
    source_page,
    source_bbox,
    source_row,
    source_col,
    schema_name,
    schema_version,
    model_version,
    extraction_timestamp,
    extraction_cost_usd,
    prompt_hash
FROM {{ source('silver', 'fields') }}
