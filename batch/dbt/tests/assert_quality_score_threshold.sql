-- Quality gate test: Gold records must meet minimum quality
-- Documents with mean confidence below 0.5 should not be published

SELECT
    document_id,
    quality_score
FROM {{ ref('gold_records') }}
WHERE quality_score < 0.5
