"""Partition skew handling for the batch pipeline.

THE PROBLEM: A 400-page PDF beside 2-page CSVs is textbook partition skew.
One executor gets stuck on a massive document while others sit idle.

SOLUTION: Size-based repartitioning.
- Estimate processing time from document size
- Salt large documents to spread across partitions
- Show before/after stage timings
"""

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F


# Size thresholds for repartitioning
LARGE_DOC_THRESHOLD = 1_000_000  # 1 MB = likely multi-page PDF
XLARGE_DOC_THRESHOLD = 10_000_000  # 10 MB = very large document

# Target partition size (in total bytes)
TARGET_PARTITION_BYTES = 5_000_000  # 5 MB per partition


def repartition_by_size(
    df: DataFrame,
    size_col: str = "size_bytes",
    target_partitions: int | None = None,
) -> DataFrame:
    """Repartition a DataFrame based on document size.

    Strategy:
    1. Assign a weight to each document based on size
    2. Use a salt column to spread large documents
    3. Repartition so total weight per partition is roughly equal

    Args:
        df: DataFrame with documents
        size_col: Column containing file size in bytes
        target_partitions: Override number of target partitions
    """
    # Calculate weight (processing time correlates with size)
    df_weighted = df.withColumn(
        "processing_weight",
        F.when(F.col(size_col) > XLARGE_DOC_THRESHOLD, 10)
        .when(F.col(size_col) > LARGE_DOC_THRESHOLD, 3)
        .otherwise(1),
    )

    if target_partitions is None:
        # Calculate target partitions from total weight
        total_weight = df_weighted.agg(F.sum("processing_weight")).collect()[0][0] or 1
        target_partitions = max(4, int(total_weight / 3))  # ~3 weight units per partition

    # Add salt for distribution
    df_salted = df_weighted.withColumn(
        "partition_salt",
        F.abs(F.hash(F.col("document_id"))) % target_partitions,
    )

    # Repartition by salt (spreads large docs across partitions)
    df_repartitioned = df_salted.repartition(target_partitions, "partition_salt")

    # Clean up temporary columns
    return df_repartitioned.drop("processing_weight", "partition_salt")


def report_skew_metrics(
    df: DataFrame,
    partition_col: str = "partition_id",
) -> dict:
    """Report partition skew metrics for diagnostics.

    Returns metrics showing before/after distribution.
    """
    # Get partition sizes
    partition_stats = (
        df.withColumn("partition_id", F.spark_partition_id())
        .groupBy("partition_id")
        .agg(
            F.count("*").alias("doc_count"),
            F.sum("size_bytes").alias("total_bytes"),
        )
    ).collect()

    if not partition_stats:
        return {"partitions": 0, "skew_ratio": 0}

    counts = [row["doc_count"] for row in partition_stats]
    bytes_per_partition = [row["total_bytes"] for row in partition_stats]

    max_count = max(counts)
    min_count = min(counts) if min(counts) > 0 else 1
    median_count = sorted(counts)[len(counts) // 2]

    return {
        "partitions": len(partition_stats),
        "skew_ratio": max_count / median_count if median_count > 0 else float("inf"),
        "max_docs_per_partition": max_count,
        "min_docs_per_partition": min(counts),
        "median_docs_per_partition": median_count,
        "max_bytes_per_partition": max(bytes_per_partition),
        "min_bytes_per_partition": min(bytes_per_partition),
    }
