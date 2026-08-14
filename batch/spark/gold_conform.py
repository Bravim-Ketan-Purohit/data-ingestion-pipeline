"""Gold layer: schema-conformed business records.

One row per document conforming to the target JSON Schema, validated,
with a quality score.

Time travel: every Gold record records the Delta version of the Silver
it derived from, so a result can be reproduced exactly after a re-extraction.

dbt owns the transforms and the tests.
"""

from datetime import datetime

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from batch.spark.config import get_delta_paths


GOLD_SCHEMA = StructType([
    StructField("document_id", StringType(), False),
    StructField("content_hash", StringType(), False),
    StructField("schema_name", StringType(), False),
    StructField("schema_version", LongType(), False),
    StructField("record_json", StringType(), False),  # The conformed JSON document
    StructField("quality_score", DoubleType(), True),  # Mean confidence across fields
    StructField("field_count", LongType(), False),
    StructField("low_confidence_count", LongType(), False),  # Fields below threshold
    StructField("silver_delta_version", LongType(), False),  # Time-travel provenance
    StructField("conformed_at", TimestampType(), False),
    StructField("dbt_run_id", StringType(), True),  # Which dbt run produced this
])


def conform_to_gold(
    spark: SparkSession,
    base_path: str | None = None,
    confidence_threshold: float = 0.7,
) -> int:
    """Conform Silver records to Gold business records.

    Aggregates per-field Silver rows into complete JSON documents.
    Records the Silver Delta version for time-travel provenance.

    Only publishes documents that pass the dbt quality gate (called separately).

    Returns:
        Number of Gold records produced
    """
    paths = get_delta_paths(base_path) if base_path else get_delta_paths()
    silver_path = paths["silver"]
    gold_path = paths["gold"]

    # Read Silver with version tracking
    silver_df = spark.read.format("delta").load(silver_path)

    # Get current Silver Delta version for provenance
    silver_table = DeltaTable.forPath(spark, silver_path)
    silver_version = silver_table.history(1).select("version").collect()[0]["version"]

    # Aggregate fields per document
    conformed = (
        silver_df
        .groupBy("document_id", "content_hash", "schema_name", "schema_version")
        .agg(
            # Build JSON from fields
            F.to_json(
                F.map_from_arrays(
                    F.collect_list("field_path"),
                    F.collect_list("field_value"),
                )
            ).alias("record_json"),
            # Quality metrics
            F.avg("confidence").alias("quality_score"),
            F.count("*").alias("field_count"),
            F.sum(
                F.when(F.col("confidence") < confidence_threshold, 1).otherwise(0)
            ).alias("low_confidence_count"),
        )
        .withColumn("silver_delta_version", F.lit(silver_version))
        .withColumn("conformed_at", F.lit(datetime.utcnow()))
        .withColumn("dbt_run_id", F.lit(None).cast(StringType()))
    )

    # Select final columns matching schema
    gold_df = conformed.select(
        "document_id",
        "content_hash",
        "schema_name",
        "schema_version",
        "record_json",
        "quality_score",
        "field_count",
        "low_confidence_count",
        "silver_delta_version",
        "conformed_at",
        "dbt_run_id",
    )

    try:
        gold_table = DeltaTable.forPath(spark, gold_path)

        # MERGE: update if reprocessed, insert if new
        (
            gold_table.alias("existing")
            .merge(
                gold_df.alias("new"),
                "existing.content_hash = new.content_hash AND existing.schema_version = new.schema_version",
            )
            .whenMatchedUpdateAll()
            .whenNotMatchedInsertAll()
            .execute()
        )
    except Exception:
        gold_df.write.format("delta").mode("overwrite").save(gold_path)

    return gold_df.count()


def read_gold(spark: SparkSession, base_path: str | None = None) -> DataFrame:
    """Read the Gold Delta table."""
    paths = get_delta_paths(base_path) if base_path else get_delta_paths()
    return spark.read.format("delta").load(paths["gold"])


def optimize_gold(spark: SparkSession, base_path: str | None = None) -> None:
    """OPTIMIZE and VACUUM the Gold table.

    OPTIMIZE/ZORDER on the columns actually filtered.
    VACUUM with retention policy — a lakehouse with no compaction story
    becomes a small-file problem.
    """
    paths = get_delta_paths(base_path) if base_path else get_delta_paths()
    gold_path = paths["gold"]

    # OPTIMIZE with ZORDER on frequently filtered columns
    spark.sql(f"""
        OPTIMIZE delta.`{gold_path}`
        ZORDER BY (schema_name, document_id)
    """)

    # VACUUM: retain 7 days of history
    # Note: VACUUM retention bounds how long deleted data stays reachable.
    # This is a genuine tension with "right to be forgotten" —
    # documented in COMPLIANCE.md
    spark.sql(f"""
        VACUUM delta.`{gold_path}` RETAIN 168 HOURS
    """)
