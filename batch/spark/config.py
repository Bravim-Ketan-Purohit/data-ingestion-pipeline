"""Spark session configuration for local and Databricks modes.

Local mode: local[4] with ~2GB memory cap.
The repo MUST run end-to-end on local Spark + local Delta with no Databricks account.
"""

from pyspark.sql import SparkSession

from pipeline.config import settings

# Delta Lake jar coordinates
DELTA_PACKAGES = "io.delta:delta-spark_2.12:3.3.0"


def create_spark_session(
    app_name: str = "data-ingestion-batch",
    local_mode: bool = True,
    memory: str = "2g",
    cores: int = 4,
) -> SparkSession:
    """Create a Spark session configured for Delta Lake.

    Args:
        app_name: Application name
        local_mode: If True, use local mode (no cluster required)
        memory: Driver memory limit
        cores: Number of local cores (only for local mode)
    """
    builder = (
        SparkSession.builder
        .appName(app_name)
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        .config("spark.jars.packages", DELTA_PACKAGES)
        # Delta-specific configs
        .config("spark.databricks.delta.schema.autoMerge.enabled", "false")
        .config("spark.sql.shuffle.partitions", "8")
    )

    if local_mode:
        builder = (
            builder
            .master(f"local[{cores}]")
            .config("spark.driver.memory", memory)
            .config("spark.executor.memory", memory)
            .config("spark.driver.maxResultSize", "512m")
        )

    return builder.getOrCreate()


def get_delta_paths(base_path: str = "data/delta") -> dict[str, str]:
    """Get Delta table paths for Bronze/Silver/Gold layers."""
    return {
        "bronze": f"{base_path}/bronze/documents",
        "silver": f"{base_path}/silver/fields",
        "gold": f"{base_path}/gold/records",
        "quarantine": f"{base_path}/quarantine/failed",
    }
