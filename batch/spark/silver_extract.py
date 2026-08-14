"""Silver layer: partitioned + typed — one row per extracted field.

Schema enforced. Deduplicated by content hash.
Schema evolution on Silver: a new field in the target schema must not require
a backfill of everything — use mergeSchema deliberately.

MERGE / upsert for re-processed documents — reprocessing a corpus after a prompt
change must UPDATE rows, not duplicate them.
"""

from datetime import datetime

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from batch.spark.config import get_delta_paths

SILVER_SCHEMA = StructType([
    StructField("field_id", StringType(), False),
    StructField("document_id", StringType(), False),
    StructField("content_hash", StringType(), False),
    StructField("field_path", StringType(), False),
    StructField("field_value", StringType(), True),
    StructField("confidence", DoubleType(), True),
    StructField("source_partition_id", StringType(), True),
    StructField("source_page", IntegerType(), True),
    StructField("source_bbox", StringType(), True),  # JSON string
    StructField("source_row", IntegerType(), True),
    StructField("source_col", IntegerType(), True),
    StructField("schema_name", StringType(), False),
    StructField("schema_version", LongType(), False),
    StructField("model_version", StringType(), False),
    StructField("extraction_timestamp", TimestampType(), False),
    StructField("extraction_cost_usd", DoubleType(), True),
    StructField("prompt_hash", StringType(), True),  # For MERGE on reprocessing
])


def upsert_to_silver(
    spark: SparkSession,
    extraction_results: list[dict],
    base_path: str | None = None,
) -> int:
    """MERGE extraction results into Silver.

    MERGE-based reprocessing: a prompt change updates rows rather than
    duplicating them. Match key is (content_hash, field_path, schema_version).

    This is the operation plain Parquet cannot do and is the honest reason
    Delta is here.

    Returns:
        Number of rows upserted
    """
    paths = get_delta_paths(base_path) if base_path else get_delta_paths()
    silver_path = paths["silver"]

    now = datetime.utcnow()
    rows = []
    for result in extraction_results:
        for field in result.get("fields", []):
            rows.append({
                "field_id": f"{result['document_id']}:{field['path']}",
                "document_id": result["document_id"],
                "content_hash": result["content_hash"],
                "field_path": field["path"],
                "field_value": str(field.get("value")),
                "confidence": field.get("confidence"),
                "source_partition_id": field.get("source_partition_id"),
                "source_page": field.get("source_page"),
                "source_bbox": str(field.get("source_bbox")) if field.get("source_bbox") else None,
                "source_row": field.get("source_row"),
                "source_col": field.get("source_col"),
                "schema_name": result.get("schema_name", "unknown"),
                "schema_version": result.get("schema_version", 1),
                "model_version": result.get("model_version", "claude-sonnet-4-20250514"),
                "extraction_timestamp": now,
                "extraction_cost_usd": field.get("cost_usd"),
                "prompt_hash": result.get("prompt_hash"),
            })

    if not rows:
        return 0

    df = spark.createDataFrame(rows, schema=SILVER_SCHEMA)

    try:
        silver_table = DeltaTable.forPath(spark, silver_path)

        # MERGE: match on content_hash + field_path + schema_version
        # Update if the prompt_hash changed (reprocessing), insert if new
        (
            silver_table.alias("existing")
            .merge(
                df.alias("new"),
                """
                existing.content_hash = new.content_hash
                AND existing.field_path = new.field_path
                AND existing.schema_version = new.schema_version
                """,
            )
            .whenMatchedUpdate(
                condition="existing.prompt_hash != new.prompt_hash OR existing.prompt_hash IS NULL",
                set={
                    "field_value": "new.field_value",
                    "confidence": "new.confidence",
                    "source_partition_id": "new.source_partition_id",
                    "source_page": "new.source_page",
                    "source_bbox": "new.source_bbox",
                    "source_row": "new.source_row",
                    "source_col": "new.source_col",
                    "model_version": "new.model_version",
                    "extraction_timestamp": "new.extraction_timestamp",
                    "extraction_cost_usd": "new.extraction_cost_usd",
                    "prompt_hash": "new.prompt_hash",
                },
            )
            .whenNotMatchedInsertAll()
            .execute()
        )
    except Exception:
        # Table doesn't exist yet — create with schema evolution enabled later
        (
            df.write
            .format("delta")
            .mode("overwrite")
            .option("mergeSchema", "true")
            .save(silver_path)
        )

    return len(rows)


def read_silver(spark: SparkSession, base_path: str | None = None) -> DataFrame:
    """Read the Silver Delta table."""
    paths = get_delta_paths(base_path) if base_path else get_delta_paths()
    return spark.read.format("delta").load(paths["silver"])
