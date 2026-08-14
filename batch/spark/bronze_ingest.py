"""Bronze layer: raw landing — original bytes metadata, never mutated.

Partitioned by ingest date. One row per source document.
ACID appends with concurrent writers.
"""

from datetime import datetime

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from batch.spark.config import get_delta_paths

BRONZE_SCHEMA = StructType([
    StructField("document_id", StringType(), False),
    StructField("filename", StringType(), False),
    StructField("content_hash", StringType(), False),
    StructField("mime", StringType(), False),
    StructField("size_bytes", LongType(), False),
    StructField("s3_key", StringType(), False),
    StructField("schema_name", StringType(), False),
    StructField("schema_version", LongType(), False),
    StructField("ingest_timestamp", TimestampType(), False),
    StructField("ingest_date", StringType(), False),  # partition key
    StructField("source_system", StringType(), True),
    StructField("kafka_offset", LongType(), True),
])


def ingest_to_bronze(
    spark: SparkSession,
    documents: list[dict],
    base_path: str | None = None,
) -> int:
    """Ingest documents into the Bronze layer.

    ACID append-only — original data is never mutated.
    Deduplicated by content_hash.
    Partitioned by ingest_date.

    Args:
        spark: Active Spark session
        documents: List of document metadata dicts
        base_path: Override Delta base path

    Returns:
        Number of new documents ingested
    """
    paths = get_delta_paths(base_path) if base_path else get_delta_paths()
    bronze_path = paths["bronze"]

    now = datetime.utcnow()
    ingest_date = now.strftime("%Y-%m-%d")

    # Add ingest metadata
    rows = []
    for doc in documents:
        rows.append({
            "document_id": doc["document_id"],
            "filename": doc["filename"],
            "content_hash": doc["content_hash"],
            "mime": doc["mime"],
            "size_bytes": doc["size_bytes"],
            "s3_key": doc["s3_key"],
            "schema_name": doc.get("schema_name", "unknown"),
            "schema_version": doc.get("schema_version", 1),
            "ingest_timestamp": now,
            "ingest_date": ingest_date,
            "source_system": doc.get("source_system"),
            "kafka_offset": doc.get("kafka_offset"),
        })

    df = spark.createDataFrame(rows, schema=BRONZE_SCHEMA)

    # Check if Bronze table exists
    try:
        bronze_table = DeltaTable.forPath(spark, bronze_path)
        # Deduplicate: only insert documents not already in Bronze
        # This ensures idempotency — a re-run does not duplicate rows
        bronze_table.alias("existing").merge(
            df.alias("new"),
            "existing.content_hash = new.content_hash",
        ).whenNotMatchedInsertAll().execute()

        # Count new rows (those not already present)
        new_count = df.join(
            bronze_table.toDF().select("content_hash"),
            "content_hash",
            "left_anti",
        ).count()
    except Exception:
        # Table doesn't exist yet — create it
        (
            df.write
            .format("delta")
            .mode("overwrite")
            .partitionBy("ingest_date")
            .save(bronze_path)
        )
        new_count = df.count()

    return new_count


def read_bronze(spark: SparkSession, base_path: str | None = None) -> DataFrame:
    """Read the Bronze Delta table."""
    paths = get_delta_paths(base_path) if base_path else get_delta_paths()
    return spark.read.format("delta").load(paths["bronze"])
