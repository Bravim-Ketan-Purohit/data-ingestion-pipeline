-- Gold layer: schema-conformed business records
-- One row per document with quality metrics

{{ config(materialized='table') }}

WITH field_stats AS (
    SELECT
        document_id,
        content_hash,
        schema_name,
        schema_version,
        COUNT(*) AS field_count,
        AVG(confidence) AS avg_confidence,
        SUM(CASE WHEN confidence < 0.7 THEN 1 ELSE 0 END) AS low_confidence_count,
        MIN(extraction_timestamp) AS earliest_extraction,
        MAX(extraction_timestamp) AS latest_extraction,
        SUM(COALESCE(extraction_cost_usd, 0)) AS total_cost_usd
    FROM {{ ref('stg_silver_fields') }}
    GROUP BY document_id, content_hash, schema_name, schema_version
)

SELECT
    document_id,
    content_hash,
    schema_name,
    schema_version,
    field_count,
    avg_confidence AS quality_score,
    low_confidence_count,
    total_cost_usd,
    earliest_extraction AS conformed_at,
    CURRENT_TIMESTAMP AS published_at
FROM field_stats
WHERE avg_confidence IS NOT NULL
